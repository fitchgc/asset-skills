from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .auth import (
    LoginSvrClient,
    clear_auth_session,
    is_login_ui_disabled,
    load_auth_session,
    login_and_provision,
    register_and_provision,
    register_login_ui_handler,
    resolve_login_svr_url,
    save_auth_session,
    set_login_ui_disabled,
    validate_login_svr_url,
)
from .client import AsynxClient
from .config import load_credentials, read_config
from .constants import DEFAULT_OUTPUT_DIR
from .errors import AsxError
from .output import emit, log
from .state import connect_db, upsert_asset, utc_now
from .tasks import run_task_submission

_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")


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
        return roots

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

            if path == "/api/auth/check-username":
                qs = parse_qs(parsed.query)
                username = (qs.get("username") or [""])[0].strip().lower()
                login_svr_override = (qs.get("login_svr_url") or [None])[0]
                if not _USERNAME_RE.match(username):
                    self._send_json(
                        AsxError(
                            "用户名格式不正确，需为 3–32 位小写字母、数字或下划线 (^[a-z0-9_]{3,32}$)",
                            code="invalid_credentials_format",
                            details={"username": username},
                        ).payload(),
                        status=400,
                    )
                    return
                try:
                    resolved_url = validate_login_svr_url(
                        login_svr_override or self.server.metadata.get("login_svr_url") or resolve_login_svr_url(read_config())
                    )
                    data = LoginSvrClient(resolved_url).check_username(username)
                    self._send_json(
                        {
                            "ok": True,
                            "username": username,
                            "exists": bool(data.get("exists")),
                            "available": bool(data.get("available")),
                            "login_svr_url": resolved_url,
                        }
                    )
                except AsxError as exc:
                    self._send_json(exc.payload(), status=400)
                return

            if path == "/api/manifest" and "manifest_path" in self.server.metadata:
                m_path = Path(self.server.metadata["manifest_path"]).resolve()
                if m_path.is_file():
                    data = m_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    return

            if path == "/api/session":
                session_data = {
                    "ok": True,
                    "mode": self.server.mode,
                    "source_url": "/api/source",
                    "source_name": Path(self.server.source_path).name if self.server.source_path else None,
                    "metadata": self.server.metadata,
                    "models": self.server.models,
                }
                self._send_json(session_data)
                return

            if path == "/api/result":
                r_path = parse_qs(parsed.query).get("path", [None])[0]
                if r_path:
                    res_file = Path(r_path).expanduser().resolve()
                    if res_file.is_file():
                        mime, _ = mimetypes.guess_type(str(res_file))
                        mime = mime or "image/png"
                        size = res_file.stat().st_size
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(size))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        with res_file.open("rb") as f:
                            while chunk := f.read(64 * 1024):
                                self.wfile.write(chunk)
                        return
                self._send_json({"ok": False, "error": {"code": "not_found", "message": "Result file not found"}}, status=404)
                return

            if path == "/api/source" and self.server.source_path:
                source_file = Path(self.server.source_path).resolve()
                if not source_file.is_file():
                    self._send_json({"ok": False, "error": {"code": "not_found", "message": "Source file not found"}}, status=404)
                    return
                mime, _ = mimetypes.guess_type(str(source_file))
                mime = mime or "application/octet-stream"
                size = source_file.stat().st_size

                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(size))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
                self.end_headers()
                with source_file.open("rb") as f:
                    while chunk := f.read(64 * 1024):
                        self.wfile.write(chunk)
                return

        # Serve static frontend files
        rel_path = path.lstrip("/") or "index.html"
        if self.server.mode == "login" and rel_path == "index.html":
            rel_path = "login.html"
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

        if path == "/api/auth/login":
            try:
                res = self.server.handle_auth_login(body)
                self._send_json(res)
                if self.server.mode == "login":
                    threading.Timer(0.25, self.server.shutdown_event.set).start()
            except AsxError as exc:
                self._send_json(exc.payload(), status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error": {"code": "login_failed", "message": str(exc)}}, status=500)
            return

        if path == "/api/auth/register":
            try:
                res = self.server.handle_auth_register(body)
                self._send_json(res)
                if self.server.mode == "login":
                    threading.Timer(0.25, self.server.shutdown_event.set).start()
            except AsxError as exc:
                self._send_json(exc.payload(), status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error": {"code": "register_failed", "message": str(exc)}}, status=500)
            return

        if path == "/api/auth/change-password":
            try:
                res = self.server.handle_auth_change_password(body)
                self._send_json(res)
            except AsxError as exc:
                self._send_json(exc.payload(), status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error": {"code": "change_password_failed", "message": str(exc)}}, status=500)
            return

        if path == "/api/auth/logout":
            try:
                res = self.server.handle_auth_logout()
                self._send_json(res)
            except AsxError as exc:
                self._send_json(exc.payload(), status=400)
            except Exception as exc:
                self._send_json({"ok": False, "error": {"code": "logout_failed", "message": str(exc)}}, status=500)
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
                if not body.get("keep_alive"):
                    threading.Timer(1.5, self.server.shutdown_event.set).start()
            except AsxError as exc:
                self._send_json(exc.payload(), status=400)
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
            except Exception:
                self.models = []

    def get_auth_status(self) -> dict[str, Any]:
        config = read_config()
        try:
            login_svr_url = validate_login_svr_url(
                self.metadata.get("login_svr_url") or resolve_login_svr_url(config)
            )
        except AsxError:
            login_svr_url = str(self.metadata.get("login_svr_url") or "")

        try:
            session = load_auth_session(allow_login_ui=False, sync_config=False)
        except AsxError:
            if self.client is not None and self.client.partner_token:
                identity: dict[str, Any] | None = None
                identity_error: dict[str, Any] | None = None
                try:
                    identity, _ = self.client.partner_identity()
                except AsxError as exc:
                    identity_error = exc.payload()["error"]
                return {
                    "ok": True,
                    "authenticated": True,
                    "login_svr_url": login_svr_url,
                    "user": config.get("user") if isinstance(config.get("user"), dict) else None,
                    "identity": identity,
                    **({"identity_error": identity_error} if identity_error else {}),
                }
            return {
                "ok": True,
                "authenticated": False,
                "login_svr_url": login_svr_url,
                "user": None,
                "identity": None,
            }

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
            "login_svr_url": session.login_svr_url,
            "user": session.user,
            "asynx": session.asynx,
            "identity": identity,
            **({"identity_error": identity_error} if identity_error else {}),
        }

    def _finish_auth_success(
        self,
        *,
        session: Any,
        identity: dict[str, Any],
    ) -> dict[str, Any]:
        self.client = AsynxClient(session.base_url, session=session)
        self.auth_result = {
            "login_svr_url": session.login_svr_url,
            "token": session.token,
            "refreshToken": session.refresh_token,
            "asynx": dict(session.asynx),
            "user": dict(session.user),
        }
        payload = {
            "ok": True,
            "authenticated": True,
            "login_svr_url": session.login_svr_url,
            "user": session.user,
            "identity": identity,
        }
        if self.mode == "login":
            self.result_payload = payload
            self.exit_code = 0
        return payload

    def handle_auth_login(self, body: dict[str, Any]) -> dict[str, Any]:
        username = str(body.get("username") or "").strip().lower()
        password = str(body.get("password") or "")
        login_svr_url = body.get("login_svr_url")
        if isinstance(login_svr_url, str) and not login_svr_url.strip():
            login_svr_url = None
        if not username or not password:
            raise AsxError("请输入用户名和密码", code="invalid_credentials_format")
        resolved_login_url = validate_login_svr_url(
            login_svr_url or self.metadata.get("login_svr_url") or resolve_login_svr_url(read_config())
        )
        session, identity = login_and_provision(
            username,
            password,
            login_svr_url=resolved_login_url,
        )
        return self._finish_auth_success(session=session, identity=identity)

    def handle_auth_register(self, body: dict[str, Any]) -> dict[str, Any]:
        username = str(body.get("username") or "").strip().lower()
        password = str(body.get("password") or "")
        nickname_raw = body.get("nickname")
        nickname = str(nickname_raw).strip() if isinstance(nickname_raw, str) and nickname_raw.strip() else None
        login_svr_url = body.get("login_svr_url")
        if isinstance(login_svr_url, str) and not login_svr_url.strip():
            login_svr_url = None

        if not _USERNAME_RE.match(username):
            raise AsxError(
                "用户名格式不正确，需为 3–32 位小写字母、数字或下划线 (^[a-z0-9_]{3,32}$)",
                code="invalid_credentials_format",
            )
        if not (6 <= len(password) <= 64):
            raise AsxError(
                "密码长度需为 6–64 位字符",
                code="invalid_credentials_format",
            )
        resolved_login_url = validate_login_svr_url(
            login_svr_url or self.metadata.get("login_svr_url") or resolve_login_svr_url(read_config())
        )
        session, identity = register_and_provision(
            username,
            password,
            nickname=nickname,
            login_svr_url=resolved_login_url,
        )
        return self._finish_auth_success(session=session, identity=identity)

    def handle_auth_change_password(self, body: dict[str, Any]) -> dict[str, Any]:
        old_pw = str(body.get("oldPassword") if body.get("oldPassword") is not None else (body.get("old_password") or ""))
        new_pw = str(body.get("newPassword") if body.get("newPassword") is not None else (body.get("new_password") or ""))
        if not old_pw:
            raise AsxError("请输入原密码", code="invalid_password_change")
        if not (6 <= len(new_pw) <= 64):
            raise AsxError("新密码长度需为 6–64 位字符", code="invalid_password_change")
        if old_pw == new_pw:
            raise AsxError("新密码不能与原密码相同", code="invalid_password_change")

        session = load_auth_session(allow_login_ui=False)
        token = session.ensure_valid_token()
        data = LoginSvrClient(session.login_svr_url).change_password(token, old_pw, new_pw)
        save_auth_session(data, login_svr_url=session.login_svr_url)
        session._apply_session_payload(data)
        self.client = AsynxClient(session.base_url, session=session)
        return {
            "ok": True,
            "user": session.user,
            "message": "密码已修改",
        }

    def handle_auth_logout(self) -> dict[str, Any]:
        clear_auth_session(keep_login_svr_url=True)
        self.client = None
        self.auth_result = None
        return {"ok": True, "authenticated": False}

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

    def _handle_region_edit_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if not self.client:
            raise AsxError("Region edit requires Asset Gateway credentials", code="not_configured")
        if not self.source_path:
            raise AsxError("Region edit requires a source image", code="missing_source_image")

        mask_data_url = body.get("mask")
        preview_data_url = body.get("annotation_preview")
        source_data_url = body.get("source_data")
        annotations = body.get("annotations", [])
        overall_prompt = body.get("prompt", "").strip()
        selected_model = body.get("model") or "gpt-image-2.5-sunburst"

        dest_dir = Path(self.output_dir).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        token = secrets.token_hex(6)
        mask_path = dest_dir / f"mask_{token}.png"
        preview_path = dest_dir / f"preview_{token}.png"
        clean_source_path = Path(self.source_path)

        if source_data_url:
            source_bytes, _ = _decode_data_url(source_data_url)
            clean_source_path = dest_dir / f"source_{token}.png"
            clean_source_path.write_bytes(source_bytes)
        if mask_data_url:
            mask_bytes, _ = _decode_data_url(mask_data_url)
            mask_path.write_bytes(mask_bytes)
        if preview_data_url:
            preview_bytes, _ = _decode_data_url(preview_data_url)
            preview_path.write_bytes(preview_bytes)

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

        # Prepare images: [source_image, annotation_preview_image]
        input_paths = [str(clean_source_path)]
        if preview_data_url and preview_path.is_file():
            input_paths.append(str(preview_path))

        from .reference_media import prepare_inputs
        images, prepared_mask = prepare_inputs(
            input_paths,
            mask=str(mask_path) if (mask_data_url and mask_path.is_file()) else None,
            keep_original=False,
        )
        request_input: dict[str, Any] = {
            "prompt": final_prompt,
            "images": images,
        }
        if prepared_mask:
            request_input["mask"] = prepared_mask

        request_body = {
            "model": selected_model,
            "input": request_input,
        }

        idempotency_key = f"asx-ui-region-{token}"
        res_payload, exit_code = run_task_submission(
            self.client,
            task_type="image.edit",
            body=request_body,
            model=selected_model,
            output_dir=str(dest_dir),
            idempotency_key=idempotency_key,
            modality="image",
        )
        # Fallback if the selected model does not support mask parameter (e.g., gemini-*-image)
        err_msg = str((res_payload.get("error") or {}).get("message", ""))
        if not res_payload.get("ok") and "mask" in err_msg and "mask" in request_input:
            fallback_input = dict(request_input)
            fallback_input.pop("mask", None)
            fallback_body = {"model": selected_model, "input": fallback_input}
            res_payload, exit_code = run_task_submission(
                self.client,
                task_type="image.edit",
                body=fallback_body,
                model=selected_model,
                output_dir=str(dest_dir),
                idempotency_key=f"asx-ui-region-nomask-{token}",
                modality="image",
            )
        return res_payload, exit_code

    def _handle_image_tool_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        image_data = body.get("image_data") or body.get("annotation_preview") or body.get("mask")
        if not image_data:
            raise AsxError("No image data provided in submission", code="missing_image_data")

        data, mime = _decode_data_url(image_data)
        ext = "png" if "png" in mime else "webp" if "webp" in mime else "jpg"

        if self.output_path:
            out_file = Path(self.output_path).expanduser().resolve()
        else:
            token = secrets.token_hex(6)
            out_file = Path(self.output_dir).expanduser().resolve() / f"processed_{token}.{ext}"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(data)

        # Record artifact if ledger is available
        try:
            conn = connect_db()
            upsert_asset(
                conn,
                local_task_id="local_ui_tool",
                remote_index=0,
                path=str(out_file),
                bytes=len(data),
            )
            conn.close()
        except Exception:
            pass

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

        data, _ = _decode_data_url(atlas_data)
        dest_dir = Path(self.output_dir).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        atlas_path = dest_dir / "atlas.png"
        manifest_path = dest_dir / "manifest.json"

        atlas_path.write_bytes(data)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
    import subprocess
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
        except Exception:
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
    except Exception:
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


def _login_static_dir() -> Path | None:
    bundled = Path(__file__).resolve().parent / "ui"
    if bundled.is_dir():
        return bundled
    dev_common = Path(__file__).resolve().parents[2] / "src" / "ui" / "common"
    if dev_common.is_dir():
        return dev_common
    return None


def launch_login_workbench_session(
    *,
    login_svr_url: str | None = None,
    open_browser: bool = True,
) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
    resolved_url = validate_login_svr_url(login_svr_url or resolve_login_svr_url(read_config()))
    effective_open = open_browser and os.environ.get("ASYNX_NO_OPEN_BROWSER", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }
    server = WorkbenchServer(
        mode="login",
        source_path=None,
        output_path=None,
        output_dir=None,
        static_dir=_login_static_dir(),
        metadata={"login_svr_url": resolved_url},
        client=None,
    )
    payload, exit_code = _run_workbench_server(server, open_browser=effective_open)
    return payload, exit_code, server.auth_result


def launch_login_workbench(login_svr_url: str | None = None) -> dict[str, Any] | None:
    """Blocking LoginUIHandler hook registered with auth.register_login_ui_handler."""
    _payload, exit_code, auth_result = launch_login_workbench_session(
        login_svr_url=login_svr_url,
        open_browser=True,
    )
    if exit_code == 0 and isinstance(auth_result, dict):
        return auth_result
    return None


def register_default_login_ui_handler() -> None:
    register_login_ui_handler(launch_login_workbench)


def configure_login_ui(*, no_login_ui: bool = False) -> None:
    set_login_ui_disabled(no_login_ui)
    if no_login_ui or is_login_ui_disabled():
        register_login_ui_handler(None)
    else:
        register_default_login_ui_handler()
