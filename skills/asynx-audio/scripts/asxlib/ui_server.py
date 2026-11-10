from __future__ import annotations

import base64
import cgi
import io
import json
import mimetypes
import os
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .client import AsynxClient
from .config import load_credentials
from .constants import DEFAULT_OUTPUT_DIR
from .errors import AsxError
from .output import emit, log
from .state import connect_db, upsert_asset, utc_now
from .tasks import run_task_submission


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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            if not self._check_auth():
                self._send_json({"ok": False, "error": {"code": "unauthorized", "message": "Unauthorized"}}, status=401)
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
        static_dir = self.server.static_dir
        if static_dir and static_dir.is_dir():
            rel_path = path.lstrip("/") or "index.html"
            target_file = (static_dir / rel_path).resolve()
            if not str(target_file).startswith(str(static_dir.resolve())):
                self.send_response(403)
                self.end_headers()
                return

            if target_file.is_file():
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

        if path == "/api/submit":
            try:
                res_payload, exit_code = self.server.handle_submit(body)
                self._send_json({"ok": True, "result": res_payload})
                self.server.result_payload = res_payload
                self.server.exit_code = exit_code
                self.server.shutdown_event.set()
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
        super().__init__((host, port), WorkbenchRequestHandler)
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
        self.exit_code: int = 0
        self.models: list[dict[str, Any]] = []

        if self.client:
            try:
                modality_filter = "image" if mode.startswith("image") or mode == "region_edit" else None
                models, _ = self.client.models(modality=modality_filter)
                self.models = models
            except Exception:
                self.models = []

    def handle_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        mode = self.mode

        if mode == "region_edit":
            return self._handle_region_edit_submit(body)
        elif mode in {"tools", "image_tools", "annotate", "image_annotate"}:
            return self._handle_image_tool_submit(body)
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
        annotations = body.get("annotations", [])
        overall_prompt = body.get("prompt", "").strip()
        selected_model = body.get("model") or "gpt-image-2.5-sunburst"

        dest_dir = Path(self.output_dir).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        token = secrets.token_hex(6)
        mask_path = dest_dir / f"mask_{token}.png"
        preview_path = dest_dir / f"preview_{token}.png"

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
        from .reference_media import prepare_inputs
        images, prepared_mask = prepare_inputs(
            [self.source_path, str(preview_path)],
            mask=str(mask_path) if mask_data_url else None,
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
        return run_task_submission(
            self.client,
            task_type="image.edit",
            body=request_body,
            model=selected_model,
            output_dir=str(dest_dir),
            idempotency_key=idempotency_key,
            modality="image",
        )

    def _handle_image_tool_submit(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        image_data = body.get("image_data")
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
    server = WorkbenchServer(
        mode=mode,
        source_path=source_path,
        output_path=output_path,
        output_dir=output_dir,
        static_dir=static_dir,
        metadata=metadata,
        client=client,
    )
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={server.session_token}#token={server.session_token}"

    log(f"Local UI Workbench available at: {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

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
