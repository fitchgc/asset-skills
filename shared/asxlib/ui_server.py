from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import runtime_env, thumb_cache
from .auth import load_auth_session, load_credential_context
from .client import AsynxClient
from .config import load_credentials, read_config
from .constants import DEFAULT_OUTPUT_DIR
from .errors import AsxError
from .output import emit, log
from .state import connect_db, record_artifact_with_lineage
from .charge_consent import consent_from_request, require_charge_consent
from .tasks import run_task_submission


_EXTRA_MIME_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "text/plain; charset=utf-8",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}


def _guess_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_MIME_TYPES:
        return _EXTRA_MIME_TYPES[ext]
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _asset_manager_modules() -> tuple[Any, Any]:
    if __package__ and not __package__.startswith("shared."):
        from . import actions, scanner
        return scanner, actions
    from src.asset_manager import actions, scanner
    return scanner, actions


def _http_status_for_error(code: str) -> int:
    if code == "path_outside_workspace":
        return 403
    if code == "need_login":
        return 401
    if code in {"asset_not_found", "file_not_found", "not_found"}:
        return 404
    if code in {"confirmation_required", "quote_unavailable"}:
        return 409  # nothing was submitted; the page asks the user and re-sends with the confirmation
    return 400


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    if not data_url.startswith("data:"):
        raise AsxError("Invalid data URL", code="invalid_data_url")
    header, encoded = data_url.split(",", 1)
    mime = header.split(";")[0].removeprefix("data:")
    return base64.b64decode(encoded), mime


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    server: WorkbenchServer

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default HTTP logging to keep agent stdout clean

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Asx-Session-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _check_auth(self) -> bool:
        auth_token = self.headers.get("X-Asx-Session-Token")
        if not auth_token:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            token_list = query.get("token")
            auth_token = token_list[0] if token_list else None
        return auth_token == self.server.session_token

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Asx-Session-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _static_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self.server.static_dir and self.server.static_dir.is_dir():
            roots.append(self.server.static_dir)
        bundled_ui = Path(__file__).resolve().parent / "ui"
        if bundled_ui.is_dir() and bundled_ui not in roots:
            roots.append(bundled_ui)
        dev_common = Path(__file__).resolve().parents[2] / "src" / "ui" / "common"
        if dev_common.is_dir() and dev_common not in roots:
            roots.append(dev_common)
        dev_ui_root = Path(__file__).resolve().parents[2] / "src" / "ui"
        if dev_ui_root.is_dir() and dev_ui_root not in roots:
            roots.append(dev_ui_root)
        return roots

    def _send_file_stream(self, target_file: Path, *, cross_origin: bool = False) -> None:
        size = target_file.stat().st_size
        mime = _guess_mime_type(target_file)
        range_header = self.headers.get("Range")
        start = 0
        end = size - 1
        status = 200
        if range_header and range_header.startswith("bytes=") and size > 0:
            spec = range_header[len("bytes="):].split(",", 1)[0].strip()
            if "-" in spec:
                s_part, e_part = spec.split("-", 1)
                try:
                    if s_part:
                        start = int(s_part)
                        end = int(e_part) if e_part else size - 1
                    elif e_part:
                        suffix_len = int(e_part)
                        start = max(0, size - suffix_len)
                        end = size - 1
                    if 0 <= start <= end < size:
                        status = 206
                except ValueError:
                    start, end, status = 0, size - 1, 200

        length = max(0, end - start + 1) if size > 0 else 0
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Access-Control-Allow-Origin", "*")
        if cross_origin:
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.end_headers()
        if length <= 0:
            return
        with target_file.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _confine_if_needed(self, raw_path: str) -> Path:
        ws_root = self.server.workspace_root()
        if ws_root is not None:
            _scanner, actions = _asset_manager_modules()
            return actions.confine_workspace_path(raw_path, ws_root)
        return Path(raw_path).expanduser().resolve()

    def _resolve_static_file(self, rel_path: str) -> tuple[Path | None, bool]:
        """Return (resolved_file, forbidden). Checks static_dir then shared/common fallbacks."""
        clean_rel = rel_path.lstrip("/")
        for root in self._static_roots():
            resolved_root = root.resolve()
            candidate = (resolved_root / clean_rel).resolve()
            if not candidate.is_relative_to(resolved_root):
                return None, True
            if candidate.is_file():
                return candidate, False
        return None, False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            if not self._check_auth():
                self._send_json({"ok": False, "error": {"code": "unauthorized", "message": "Unauthorized"}}, status=401)
                return

            if path == "/api/auth/status":
                self._send_json(self.server.get_auth_status())
                return

            if path == "/api/assets":
                ws_root = self.server.workspace_root()
                if ws_root is None:
                    self._send_json(
                        {"ok": False, "error": {"code": "workspace_not_configured", "message": "Workspace root is not configured"}},
                        status=400,
                    )
                    return
                qs = parse_qs(parsed.query)
                category = (qs.get("category") or ["all"])[0]
                source = (qs.get("source") or ["all"])[0]
                managed_only = (qs.get("managed_only") or [""])[0].strip().lower() in {"1", "true", "yes"}
                external_only = (qs.get("external_only") or [""])[0].strip().lower() in {"1", "true", "yes"}
                query_text = (qs.get("query") or [None])[0]
                no_cache = (qs.get("no_cache") or [""])[0].strip().lower() in {"1", "true", "yes"}
                try:
                    scanner, _actions = _asset_manager_modules()
                    payload = scanner.scan_workspace(
                        ws_root,
                        category=category,
                        managed_only=managed_only,
                        external_only=external_only,
                        source=source,
                        query=query_text,
                        use_cache=not no_cache,
                        lazy_sha=True,
                    )
                    self._send_json(payload)
                except AsxError as exc:
                    self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                return

            if path == "/api/thumb":
                raw_target = (parse_qs(parsed.query).get("path") or [""])[0]
                try:
                    source = self._confine_if_needed(raw_target) if raw_target else None
                    hit = thumb_cache.read_thumb(source) if source and source.is_file() else None
                except (AsxError, OSError):
                    hit = None
                if hit is None:
                    self._send_json({"ok": False, "error": {"code": "not_found", "message": "No cached thumbnail"}}, status=404)
                    return
                data, etag = hit
                if self.headers.get("If-None-Match") == f'"{etag}"':
                    self.send_response(304)
                    self.send_header("ETag", f'"{etag}"')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("ETag", f'"{etag}"')
                self.send_header("Cache-Control", "private, max-age=0, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
                return

            if path == "/api/thumb-cache":
                self._send_json({"ok": True, **thumb_cache.stats()})
                return

            if path == "/api/lineage" or path.startswith("/api/lineage/"):
                qs = parse_qs(parsed.query)
                target = (
                    unquote(path[len("/api/lineage/"):])
                    if path.startswith("/api/lineage/")
                    else (qs.get("target") or [""])[0]
                )
                ws_root = self.server.workspace_root()
                try:
                    scanner, _actions = _asset_manager_modules()
                    payload = scanner.asset_lineage_report(target, root=ws_root)
                    self._send_json(payload)
                except AsxError as exc:
                    self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                return

            if path == "/api/workspace-file":
                ws_root = self.server.workspace_root()
                if ws_root is None:
                    self._send_json(
                        {"ok": False, "error": {"code": "workspace_not_configured", "message": "Workspace root is not configured"}},
                        status=400,
                    )
                    return
                qs = parse_qs(parsed.query)
                raw_target = (qs.get("path") or [""])[0]
                if not raw_target:
                    self._send_json(
                        {"ok": False, "error": {"code": "missing_path", "message": "Missing path parameter"}},
                        status=400,
                    )
                    return
                try:
                    target_file = self._confine_if_needed(raw_target)
                except AsxError as exc:
                    self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                    return
                if not target_file.is_file():
                    self._send_json({"ok": False, "error": {"code": "not_found", "message": "Workspace file not found"}}, status=404)
                    return
                self._send_file_stream(target_file, cross_origin=True)
                return

            if path == "/api/manifest":
                qs = parse_qs(parsed.query)
                req_manifest = (qs.get("path") or qs.get("manifest_path") or [None])[0]
                m_path: Path | None = None
                if req_manifest:
                    try:
                        m_path = self._confine_if_needed(req_manifest)
                    except AsxError as exc:
                        self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                        return
                elif "manifest_path" in self.server.metadata:
                    m_path = Path(self.server.metadata["manifest_path"]).resolve()
                if m_path is not None and m_path.is_file():
                    data = m_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._send_json({"ok": False, "error": {"code": "not_found", "message": "Manifest not found"}}, status=404)
                return

            if path == "/api/session":
                qs = parse_qs(parsed.query)
                req_source = (qs.get("source_path") or [None])[0]
                req_mode = (qs.get("mode") or [None])[0]
                req_manifest = (qs.get("manifest_path") or [None])[0]
                effective_source = self.server.source_path
                source_url = "/api/source"
                if req_source:
                    try:
                        resolved_source = self._confine_if_needed(req_source)
                    except AsxError as exc:
                        self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                        return
                    effective_source = str(resolved_source)
                    source_url = f"/api/source?token={quote(self.server.session_token)}&path={quote(effective_source)}"
                meta = dict(self.server.metadata)
                if req_manifest:
                    try:
                        meta["manifest_path"] = str(self._confine_if_needed(req_manifest))
                    except AsxError as exc:
                        self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                        return
                models = self.server.get_models_best_effort() if self.server.mode == "asset_manager" else self.server.models
                session_data = {
                    "ok": True,
                    "mode": req_mode or self.server.mode,
                    "source_url": source_url,
                    "source_path": effective_source,
                    "source_name": Path(effective_source).name if effective_source else None,
                    "metadata": meta,
                    "models": models,
                    "platform": sys.platform,
                }
                self._send_json(session_data)
                return

            if path == "/api/result":
                r_path = parse_qs(parsed.query).get("path", [None])[0]
                if r_path:
                    try:
                        res_file = self._confine_if_needed(r_path)
                    except AsxError as exc:
                        self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                        return
                    if res_file.is_file():
                        self._send_file_stream(res_file, cross_origin=True)
                        return
                self._send_json({"ok": False, "error": {"code": "not_found", "message": "Result file not found"}}, status=404)
                return

            if path == "/api/source":
                qs = parse_qs(parsed.query)
                req_source = (qs.get("path") or [None])[0]
                source_file: Path | None = None
                if req_source:
                    if self.server.workspace_root() is not None:
                        try:
                            source_file = self._confine_if_needed(req_source)
                        except AsxError as exc:
                            self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
                            return
                    elif self.server.source_path and Path(req_source).expanduser().resolve() == Path(self.server.source_path).resolve():
                        source_file = Path(self.server.source_path).resolve()
                    else:
                        self._send_json({"ok": False, "error": {"code": "path_outside_workspace", "message": "Source path override forbidden"}}, status=403)
                        return
                elif self.server.source_path:
                    source_file = Path(self.server.source_path).resolve()

                if source_file is None or not source_file.is_file():
                    self._send_json({"ok": False, "error": {"code": "not_found", "message": "Source file not found"}}, status=404)
                    return
                self._send_file_stream(source_file, cross_origin=True)
                return

        # Serve static frontend files
        rel_path = path.lstrip("/") or "index.html"
        target_file, forbidden = self._resolve_static_file(rel_path)
        if forbidden:
            self.send_response(403)
            self.end_headers()
            return
        if target_file is not None:
            mime, _ = mimetypes.guess_type(str(target_file))
            mime = mime or "application/octet-stream"
            size = target_file.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with target_file.open("rb") as f:
                while chunk := f.read(64 * 1024):
                    self.wfile.write(chunk)
            return

        self._send_json({"ok": False, "error": {"code": "not_found", "message": "Not found"}}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._check_auth():
            self._send_json({"ok": False, "error": {"code": "unauthorized", "message": "Unauthorized"}}, status=401)
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": {"code": "invalid_json", "message": "Invalid JSON"}}, status=400)
            return

        if path == "/api/cancel":
            self._send_json({"ok": True, "canceled": True})
            self.server.result_payload = {
                "ok": False,
                "error": {"code": "interrupted", "message": "Workbench session cancelled by user"},
            }
            self.server.exit_code = 130
            self.server.shutdown_event.set()
            return

        if path == "/api/thumb":
            try:
                source = self._confine_if_needed(str(body.get("path") or ""))
                if not source.is_file():
                    raise AsxError("Source file not found", code="not_found")
                data, _mime = _decode_data_url(str(body.get("image_data") or ""))
                thumb_cache.write_thumb(source, data)
                self._send_json({"ok": True})
            except AsxError as exc:
                self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
            except (ValueError, OSError) as exc:
                self._send_json({"ok": False, "error": {"code": "invalid_thumbnail", "message": str(exc)}}, status=400)
            return

        if path == "/api/thumb-cache/clear":
            self._send_json({"ok": True, **thumb_cache.clear()})
            return

        if path == "/api/runtime/prepare":
            # Downloads the hash-locked Pillow runtime. Only the page's confirmation dialog sends this,
            # after the user clicked "confirm"; the agent never calls it on its own.
            if body.get("user_confirmed") is not True:
                self._send_json(
                    {"ok": False, "error": {"code": "confirmation_required", "message": "准备运行环境需要用户在页面上确认"}},
                    status=409,
                )
                return
            try:
                self._send_json(runtime_env.prepare_runtime())
            except AsxError as exc:
                self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
            return

        if path == "/api/manager/action":
            ws_root = self.server.workspace_root()
            if ws_root is None:
                self._send_json(
                    {"ok": False, "error": {"code": "workspace_not_configured", "message": "Workspace root is not configured"}},
                    status=400,
                )
                return
            try:
                _scanner, actions = _asset_manager_modules()
                res = actions.dispatch_manager_action(
                    body,
                    workspace_root=ws_root,
                    require_client=self.server.require_client,
                )
                self._send_json(res)
            except AsxError as exc:
                self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
            except Exception as exc:
                self._send_json({"ok": False, "error": {"code": "action_failed", "message": str(exc)}}, status=500)
            return

        if path == "/api/submit":
            try:
                import importlib
                from . import reference_media
                importlib.reload(reference_media)
                res_payload, exit_code = self.server.handle_submit(body)
                if not res_payload.get("ok", True) or exit_code != 0:
                    err_info = res_payload.get("error") or {"code": "task_failed", "message": "任务执行失败"}
                    self._send_json({"ok": False, "error": err_info, "result": res_payload}, status=400)
                    return
                self._send_json({"ok": True, "result": res_payload})
                self.server.result_payload = res_payload
                self.server.exit_code = exit_code
                if self.server.mode != "asset_manager" and not body.get("keep_alive"):
                    threading.Timer(1.5, self.server.shutdown_event.set).start()
            except AsxError as exc:
                self._send_json(exc.payload(), status=_http_status_for_error(exc.code))
            except Exception as exc:
                self._send_json({"ok": False, "error": {"code": "submit_failed", "message": str(exc)}}, status=500)
            return

        self._send_json({"ok": False, "error": {"code": "not_found", "message": "Not found"}}, status=404)


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(
        self,
        *,
        mode: str,
        source_path: str | None,
        output_path: str | None,
        output_dir: str | None,
        static_dir: Path | None,
        metadata: dict[str, Any] | None = None,
        client: AsynxClient | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if port != 0:
            super().__init__((host, port), WorkbenchRequestHandler)
        else:
            # 优先使用固定的默认端口 51888，被占用时顺延，保证会话链接稳定
            initialized = False
            last_err = None
            for p in list(range(51888, 51901)) + [0]:
                try:
                    super().__init__((host, p), WorkbenchRequestHandler)
                    initialized = True
                    break
                except OSError as err:
                    last_err = err
                    continue
            if not initialized and last_err:
                raise last_err
        self.mode = mode
        self.source_path = source_path
        self.output_path = output_path
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.static_dir = static_dir
        self.metadata = metadata or {}
        self.client = client
        self.session_token = secrets.token_hex(24)
        self.shutdown_event = threading.Event()
        self.result_payload: dict[str, Any] = {}
        self.auth_result: dict[str, Any] | None = None
        self.exit_code: int = 0
        self.models: list[dict[str, Any]] = []

        if self.client:
            try:
                modality_filter = "image" if mode.startswith("image") or mode == "region_edit" else None
                models, _ = self.client.models(modality=modality_filter)
                self.models = models
            except (AsxError, OSError, ValueError):
                self.models = []

    def get_auth_status(self) -> dict[str, Any]:
        try:
            origin = load_credential_context()["origin"]
        except AsxError:
            origin = ""
        try:
            session = load_auth_session()
        except AsxError as exc:
            return {"ok": True, "authenticated": False, "origin": origin, "identity": None,
                    "error": exc.payload()["error"]}
        gw_client = AsynxClient(session.base_url, session=session)
        identity = None
        identity_error = None
        try:
            identity, _ = gw_client.partner_identity()
        except AsxError as exc:
            identity_error = exc.payload()["error"]
        return {
            "ok": True,
            "authenticated": True,
            "origin": session.origin,
            "asynx": session.asynx,
            "identity": identity,
            **({"identity_error": identity_error} if identity_error else {}),
        }

    def workspace_root(self) -> Path | None:
        raw = self.metadata.get("workspace_root") if isinstance(self.metadata, dict) else None
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser().resolve()
        return None

    def require_client(self) -> AsynxClient:
        if self.client is not None:
            return self.client
        from .config import load_client

        client = load_client()
        self.client = client
        return client

    def get_models_best_effort(self) -> list[dict[str, Any]]:
        if self.models:
            return self.models
        client = self.client
        if client is None:
            try:
                client = self.require_client()
            except (AsxError, OSError, ValueError):
                return []
        try:
            models, _ = client.models()
            self.models = models
            return models
        except (AsxError, OSError, ValueError):
            return []

    def _resolve_submit_context(self, body: dict[str, Any]) -> tuple[str | None, Path, Path | None]:
        ws_root = self.workspace_root()
        raw_source = body.get("source_path")
        raw_dir = body.get("output_dir")
        raw_out = body.get("output_path")

        if ws_root is not None:
            _scanner, actions = _asset_manager_modules()
            if isinstance(raw_source, str) and raw_source.strip():
                effective_source = str(actions.confine_workspace_path(raw_source, ws_root))
            else:
                effective_source = self.source_path
            if isinstance(raw_dir, str) and raw_dir.strip():
                dest_dir = actions.confine_workspace_path(raw_dir, ws_root)
            elif self.output_dir and Path(self.output_dir).expanduser().resolve().is_relative_to(ws_root):
                dest_dir = Path(self.output_dir).expanduser().resolve()
            else:
                dest_dir = (ws_root / "generated-assets").resolve()
            if isinstance(raw_out, str) and raw_out.strip():
                out_file: Path | None = actions.confine_workspace_path(raw_out, ws_root)
            else:
                out_file = Path(self.output_path).expanduser().resolve() if self.output_path else None
            return effective_source, dest_dir, out_file

        effective_source = str(Path(raw_source).expanduser().resolve()) if isinstance(raw_source, str) and raw_source.strip() else self.source_path
        dest_dir = Path(raw_dir or self.output_dir).expanduser().resolve()
        out_file = Path(raw_out or self.output_path).expanduser().resolve() if (raw_out or self.output_path) else None
        return effective_source, dest_dir, out_file

    def handle_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        mode = body.get("mode") or self.mode

        if mode in {"tools", "image_tools", "annotate", "image_annotate"} or body.get("recipe") or (
            "image_data" in body and "mask" not in body and "annotations" not in body and "prompt" not in body
        ):
            return self._handle_image_tool_submit(body)
        elif mode == "region_edit":
            return self._handle_region_edit_submit(body)
        elif mode in {"video_frame_sequence", "frame_sequence", "model3d_render_sequence", "render_sequence"}:
            return self._handle_atlas_submit(body)
        elif mode in {"model3d_viewer", "viewer"}:
            return {"ok": True, "viewer": "closed"}, 0
        else:
            raise AsxError(f"Unsupported workbench mode: {mode}", code="unsupported_workbench_mode")

    def _edit_aspect_ratio(self, client: Any, model_name: str, size: Any) -> str | None:
        if not size:
            return None
        try:
            from .images import _cached_models, capabilities, nearest_aspect_ratio

            models = _cached_models(client.base_url) if client else None
            for item in models or []:
                if (
                    "image.edit" in item.get("task_types", [])
                    and str(item.get("name", "")).casefold() == model_name.casefold()
                ):
                    return nearest_aspect_ratio(
                        size[0], size[1], capabilities(item).get("aspect_ratios")
                    )
        except Exception:
            return None
        return None

    def _handle_region_edit_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        client = self.require_client() if self.mode == "asset_manager" else self.client
        if not client:
            raise AsxError("Region edit requires Asset Gateway credentials", code="not_configured")
        effective_source, dest_dir, _out_file = self._resolve_submit_context(body)
        if not effective_source:
            raise AsxError("Region edit requires a source image", code="missing_source_image")

        mask_data_url = body.get("mask")
        preview_data_url = body.get("annotation_preview")
        source_data_url = body.get("source_data")
        annotations = body.get("annotations", [])
        overall_prompt = body.get("prompt", "").strip()
        selected_model = body.get("model") or "gpt-image-2.5-sunburst"

        dest_dir.mkdir(parents=True, exist_ok=True)

        token = secrets.token_hex(6)

        # Build structured prompt
        prompt_parts = []
        if overall_prompt:
            prompt_parts.append(overall_prompt)
        instruction_lines = [
            f"- {a['instruction']}" for a in annotations if isinstance(a, dict) and a.get("instruction")
        ]
        if instruction_lines:
            prompt_parts.append("Image edit annotations:\n" + "\n".join(instruction_lines))
        final_prompt = "\n\n".join(prompt_parts) or "Edit the marked regions"

        # Prepare images: [source_image, annotation_preview_image]. The page's mask, preview and
        # clean source only feed the request, so they live in a scratch dir, never the project.
        from .reference_media import prepare_inputs
        source_sizes: list[Any] = []
        with tempfile.TemporaryDirectory(prefix="asx-region-") as scratch:
            scratch_dir = Path(scratch)
            input_paths = [effective_source]
            if source_data_url:
                source_bytes, _ = _decode_data_url(source_data_url)
                (scratch_dir / "source.png").write_bytes(source_bytes)
                input_paths = [str(scratch_dir / "source.png")]
            if preview_data_url:
                preview_bytes, _ = _decode_data_url(preview_data_url)
                (scratch_dir / "preview.png").write_bytes(preview_bytes)
                input_paths.append(str(scratch_dir / "preview.png"))
            mask_path: str | None = None
            if mask_data_url:
                mask_bytes, _ = _decode_data_url(mask_data_url)
                (scratch_dir / "mask.png").write_bytes(mask_bytes)
                mask_path = str(scratch_dir / "mask.png")
            images, prepared_mask = prepare_inputs(
                input_paths,
                mask=mask_path,
                keep_original=False,
                source_sizes=source_sizes,
            )
        request_input: dict[str, Any] = {
            "prompt": final_prompt,
            "images": images,
        }
        # 保持原图画幅：按源图尺寸选择模型支持的最接近比例
        aspect = self._edit_aspect_ratio(client, selected_model, source_sizes[0] if source_sizes else None)
        if aspect:
            request_input["aspect_ratio"] = aspect
        if prepared_mask:
            request_input["mask"] = prepared_mask

        request_body = {
            "model": selected_model,
            "input": request_input,
        }

        idempotency_key = f"asx-ui-region-{token}"
        parent_paths = [effective_source] if effective_source else []
        consent_args = consent_from_request(body)
        consent = require_charge_consent(
            client, task_type="image.edit", body=request_body, model=selected_model, args=consent_args
        )
        ui_meta = {"workbench_mode": "region_edit", "prompt": final_prompt, "charge_consent": consent}
        res_payload, exit_code = run_task_submission(
            client,
            task_type="image.edit",
            body=request_body,
            model=selected_model,
            output_dir=str(dest_dir),
            idempotency_key=idempotency_key,
            modality="image",
            parent_paths=parent_paths,
            metadata=ui_meta,
            charge_consent=consent,
        )
        # Fallback if the selected model does not support mask parameter (e.g., gemini-*-image)
        err_msg = str((res_payload.get("error") or {}).get("message", ""))
        if not res_payload.get("ok") and "mask" in err_msg and "mask" in request_input:
            fallback_input = dict(request_input)
            fallback_input.pop("mask", None)
            fallback_body = {"model": selected_model, "input": fallback_input}
            # A different request: quoted again; the user's confirmation still has to cover it.
            consent = require_charge_consent(
                client, task_type="image.edit", body=fallback_body, model=selected_model, args=consent_args
            )
            ui_meta = {**ui_meta, "charge_consent": consent}
            res_payload, exit_code = run_task_submission(
                client,
                task_type="image.edit",
                body=fallback_body,
                model=selected_model,
                output_dir=str(dest_dir),
                idempotency_key=f"asx-ui-region-nomask-{token}",
                modality="image",
                parent_paths=parent_paths,
                metadata=ui_meta,
                charge_consent=consent,
            )
        return res_payload, exit_code

    def _handle_image_tool_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        image_data = body.get("image_data") or body.get("annotation_preview") or body.get("mask")
        if not image_data:
            raise AsxError("No image data provided in submission", code="missing_image_data")

        effective_source, dest_dir, out_file = self._resolve_submit_context(body)
        data, mime = _decode_data_url(image_data)
        ext = "png" if "png" in mime else "webp" if "webp" in mime else "jpg"

        if out_file is None:
            token = secrets.token_hex(6)
            out_file = dest_dir / f"processed_{token}.{ext}"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(data)

        mode = str(body.get("mode") or self.mode)
        operation = "image_annotate" if mode in {"annotate", "image_annotate"} else "image_tools"
        parent_paths = [effective_source] if effective_source else []
        conn = connect_db()
        try:
            record_artifact_with_lineage(
                conn,
                file_path=str(out_file),
                modality="image",
                operation=operation,
                source_kind="ui_export",
                category="image",
                mime=mime,
                size_bytes=len(data),
                metadata={"recipe": body.get("recipe"), "workbench_mode": mode},
                parent_paths=parent_paths,
                model="ui",
            )
        finally:
            conn.close()

        return {
            "ok": True,
            "image": {
                "path": str(out_file),
                "format": ext,
                "size_bytes": len(data),
                "recipe": body.get("recipe"),
            },
            "files": [str(out_file)],
        }, 0

    def _handle_atlas_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        atlas_data = body.get("atlas")
        manifest = body.get("manifest")
        if not atlas_data or not manifest:
            raise AsxError("Atlas PNG and Manifest JSON required", code="missing_atlas_data")

        effective_source, dest_dir, _out_file = self._resolve_submit_context(body)
        if self.mode == "asset_manager" and not body.get("output_dir"):
            dest_dir = dest_dir / f"sequence_{secrets.token_hex(4)}"
        data, _ = _decode_data_url(atlas_data)
        dest_dir.mkdir(parents=True, exist_ok=True)

        atlas_path = dest_dir / "atlas.png"
        manifest_path = dest_dir / "manifest.json"

        atlas_path.write_bytes(data)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        mode = str(body.get("mode") or self.mode)
        if mode in {"model3d_render_sequence", "render_sequence"}:
            modality = "3d"
            operation = "render_sequence"
        else:
            modality = "video"
            operation = "frame_sequence"
        extra_meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        ledger_meta = {
            **extra_meta,
            "manifest_path": str(manifest_path),
            "frames": len(manifest.get("frames", {})),
            "workbench_mode": mode,
        }
        parent_paths = [effective_source] if effective_source else []
        conn = connect_db()
        try:
            record_artifact_with_lineage(
                conn,
                file_path=str(atlas_path),
                modality=modality,
                operation=operation,
                source_kind="ui_export",
                category="frame_sequence",
                mime="image/png",
                size_bytes=len(data),
                metadata=ledger_meta,
                parent_paths=parent_paths,
                model="ui",
            )
        finally:
            conn.close()

        return {
            "ok": True,
            "atlas": {
                "path": str(atlas_path),
                "manifest_path": str(manifest_path),
                "frames": len(manifest.get("frames", {})),
                "metadata": body.get("metadata", {}),
            },
        }, 0


def _safe_open_browser(url: str) -> bool:
    """尝试在系统浏览器中打开 URL，静默捕获无桌面环境或 AppleScript 权限错误，避免 stderr 污染终端。"""
    import sys

    if sys.platform == "darwin":
        try:
            res = subprocess.run(
                ["/usr/bin/open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if res.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        # 在 macOS 上如果 open 失败（例如无 GUI 权限的后台守护进程），webbrowser 会尝试多个 AppleScript 方案并在 stderr 抛错，此处直接静默返回
        return False

    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        try:
            os.dup2(devnull_fd, 2)
            return bool(webbrowser.open(url))
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
            os.close(devnull_fd)
    except (OSError, webbrowser.Error):
        return False


def _run_workbench_server(
    server: WorkbenchServer,
    *,
    open_browser: bool,
) -> tuple[dict[str, Any], int]:
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={server.session_token}#token={server.session_token}"

    log(f"Local UI Workbench available at: {url}")
    if open_browser:
        _safe_open_browser(url)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        while not server.shutdown_event.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        server.result_payload = {
            "ok": False,
            "error": {"code": "interrupted", "message": "Workbench interrupted"},
        }
        server.exit_code = 130
    finally:
        server.shutdown()
        server.server_close()

    return server.result_payload, server.exit_code


def launch_workbench(
    *,
    mode: str,
    source_path: str | None = None,
    output_path: str | None = None,
    output_dir: str | None = None,
    static_dir: Path | None = None,
    metadata: dict[str, Any] | None = None,
    client: AsynxClient | None = None,
    open_browser: bool = True,
) -> tuple[dict[str, Any], int]:
    """Start local ephemeral workbench HTTP server and block until user submits or cancels."""
    effective_open = open_browser and os.environ.get("ASYNX_NO_OPEN_BROWSER", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }
    server = WorkbenchServer(
        mode=mode,
        source_path=source_path,
        output_path=output_path,
        output_dir=output_dir,
        static_dir=static_dir,
        metadata=metadata,
        client=client,
    )
    return _run_workbench_server(server, open_browser=effective_open)
