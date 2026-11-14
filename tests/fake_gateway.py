from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse


class FakeStorageHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.server.received_headers.append(dict(self.headers))
        path = urlparse(self.path).path
        data = self.server.objects.get(path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        content_type = self.server.object_mimes.get(path, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeStorageServer(HTTPServer):
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), FakeStorageHandler)
        self.objects: dict[str, bytes] = {}
        self.object_mimes: dict[str, str] = {}
        self.received_headers: list[dict[str, str]] = []


def run_fake_storage() -> tuple[FakeStorageServer, str, threading.Thread]:
    server = FakeStorageServer()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, url, thread


class FakeGatewayHandler(BaseHTTPRequestHandler):
    models_response = [
        {
            "name": "gpt-image-2.5-sunburst",
            "modality": "image",
            "task_types": ["image.generate", "image.edit"],
            "capabilities": ["text_to_image", "image_to_image", "inpainting"],
        },
        {
            "name": "suno-v3",
            "modality": "audio",
            "task_types": ["audio.music"],
            "capabilities": ["text_to_music"],
        },
        {
            "name": "elevenlabs-v2",
            "modality": "audio",
            "task_types": ["audio.speech"],
            "capabilities": ["text_to_speech"],
        },
        {
            "name": "audiocraft-sfx",
            "modality": "audio",
            "task_types": ["audio.sfx"],
            "capabilities": ["text_to_sfx"],
        },
        {
            "name": "kling-video-v1",
            "modality": "video",
            "task_types": ["video.generate", "video.edit"],
            "capabilities": ["text_to_video", "image_to_video"],
        },
        {
            "name": "tripo-3d-v2",
            "modality": "3d",
            "task_types": ["3d.generate", "3d.rig_check", "3d.rig", "3d.retarget", "3d.texture", "3d.convert"],
            "capabilities": ["text_to_3d", "image_to_3d"],
        },
    ]

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress server logging during tests

    def _send_envelope(
        self,
        data: Any,
        *,
        status: int = 200,
        request_id: str = "req-fake-1",
        code: str = "ok",
        message: str | None = None,
    ) -> None:
        body = json.dumps({
            "code": code,
            "message": message,
            "data": data,
            "request_id": request_id,
        }).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", request_id)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw_json(self, payload: dict[str, Any], *, status: int = 200, request_id: str = "req-fake-1") -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", request_id)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authenticate(self, method: str, path: str) -> bool:
        self.server.received_requests.append((method, path, dict(self.headers)))
        if "Authorization" in self.headers:
            self._send_envelope(
                {},
                status=401,
                code="ambiguous_credentials",
                message="Authorization header must not be sent alongside X-Asynx-Partner-Token",
                request_id="req-err-ambig",
            )
            return False

        header_name = self.server.token_header
        token = self.headers.get(header_name)
        if not token:
            self._send_envelope(
                {},
                status=401,
                code="invalid_partner_token",
                message="Missing partner token header",
                request_id="req-err-missing",
            )
            return False

        if self.server.fail_next_auth_count > 0:
            self.server.fail_next_auth_count -= 1
            self._send_envelope(
                {},
                status=401,
                code="invalid_partner_token",
                message="Token expired or rotated",
                request_id="req-err-401",
            )
            return False

        if token in self.server.expired_tokens:
            self._send_envelope(
                {},
                status=401,
                code="invalid_partner_token",
                message="Partner token expired",
                request_id="req-err-expired",
            )
            return False

        if self.server.valid_tokens is not None and token not in self.server.valid_tokens:
            self._send_envelope(
                {},
                status=401,
                code="invalid_partner_token",
                message="Invalid partner token",
                request_id="req-err-invalid",
            )
            return False

        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._authenticate("GET", path):
            return

        if path == "/v1/partner/identity":
            self._send_envelope(self.server.partner_identity_response, request_id="req-identity-1")
            return

        if path == "/v1/tasks/models":
            self._send_envelope({"items": self.models_response}, request_id="req-models-123")
            return

        if path.startswith("/v1/tasks/models/") and path.endswith("/guide"):
            model_name = path.split("/")[4]
            self._send_envelope(
                {
                    "model": model_name,
                    "parameters": {"prompt": {"type": "string", "required": True}},
                },
                request_id=f"req-guide-{model_name}",
            )
            return

        if path.startswith("/v1/tasks/") and "/assets/" in path:
            parts = path.split("/")
            task_id = parts[3]
            index = int(parts[5])
            key = f"{task_id}:{index}"
            redirect_url = self.server.redirect_urls.get(key)
            if redirect_url:
                self.send_response(302)
                self.send_header("Location", redirect_url)
                self.end_headers()
                return

            asset_data = self.server.asset_data.get(key, b"FAKE_ASSET_CONTENT")
            content_type = self.server.asset_mimes.get(key, "image/png")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(asset_data)))
            self.end_headers()
            self.wfile.write(asset_data)
            return

        if path.startswith("/v1/tasks/"):
            task_id = path.split("/")[-1]
            seq = self.server.task_poll_sequences.get(task_id)
            if seq:
                next_status = seq.pop(0)
                task = dict(self.server.tasks.get(task_id, {"id": task_id}))
                task["status"] = next_status
                if next_status == "blocked":
                    task["error"] = {"code": "safety_blocked", "message": "Content blocked by safety policy"}
                elif next_status == "succeeded" and "result" not in task:
                    task["result"] = {"assets": [{"index": 0, "download_url": None}]}
                self.server.tasks[task_id] = task
            else:
                task = self.server.tasks.get(
                    task_id,
                    {
                        "id": task_id,
                        "status": "succeeded",
                        "result": {"assets": [{"index": 0, "download_url": None}]},
                    },
                )
            self._send_envelope(task, request_id=f"req-task-{task_id}")
            return

        if path == "/v1/tasks":
            items = list(self.server.tasks.values())
            self._send_envelope({"items": items}, request_id="req-tasks-list")
            return

        self._send_envelope({}, status=404, code="not_found", message="Not found", request_id="req-404")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._authenticate("POST", path):
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b""

        if path == "/v1/files":
            file_id = f"file_{len(self.server.uploaded_files) + 1}"
            self.server.uploaded_files[file_id] = raw_body
            self._send_raw_json(
                {
                    "id": file_id,
                    "bytes": len(raw_body),
                    "content_type": self.headers.get("Content-Type", ""),
                },
                status=200,
                request_id=f"req-file-{file_id}",
            )
            return

        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}

        if path == "/v1/tasks/quote":
            self._send_envelope(
                {
                    "model": body.get("model", "gpt-image-2.5-sunburst"),
                    "currency": "USD",
                    "estimated_amount": "0.12000000",
                    "charge_amount": "0.12000000",
                },
                request_id="req-quote-1",
            )
            return

        if path.startswith("/v1/tasks/") and path.endswith("/cancel"):
            task_id = path.split("/")[3]
            task = self.server.tasks.get(task_id, {"id": task_id, "status": "canceled"})
            task["status"] = "canceled"
            self._send_envelope(task, request_id=f"req-cancel-{task_id}")
            return

        if path == "/v1/tasks":
            task_id = f"task_{len(self.server.tasks) + 1}"
            task_type = body.get("task_type", "image.generate")
            status = self.server.default_submit_status
            task: dict[str, Any] = {
                "id": task_id,
                "task_type": task_type,
                "model": body.get("model", "default-model"),
                "status": status,
                "input": body.get("input", {}),
            }
            if status == "succeeded":
                task["result"] = {"assets": [{"index": 0, "download_url": None}]}
            if self.server.next_task_poll_sequence:
                self.server.task_poll_sequences[task_id] = list(self.server.next_task_poll_sequence)
                self.server.next_task_poll_sequence = None
            self.server.tasks[task_id] = task
            self.server.submissions.append(body)
            self._send_envelope(task, status=202, request_id=f"req-submit-{task_id}")
            return

        self._send_envelope({}, status=404, code="not_found", message="Not found", request_id="req-404")


class FakeGatewayServer(HTTPServer):
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), FakeGatewayHandler)
        self.token_header: str = "X-Asynx-Partner-Token"
        self.valid_tokens: set[str] | None = None
        self.expired_tokens: set[str] = set()
        self.fail_next_auth_count: int = 0
        self.default_submit_status: str = "succeeded"
        self.next_task_poll_sequence: list[str] | None = None
        self.task_poll_sequences: dict[str, list[str]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.asset_data: dict[str, bytes] = {}
        self.asset_mimes: dict[str, str] = {}
        self.redirect_urls: dict[str, str] = {}
        self.uploaded_files: dict[str, bytes] = {}
        self.submissions: list[dict[str, Any]] = []
        self.received_requests: list[tuple[str, str, dict[str, str]]] = []
        self.partner_identity_response: dict[str, Any] = {
            "api_key": {"status": "active", "concurrency_limit": 5},
            "usage": {"active_tasks": 0},
            "budget": {"remaining_amount": "50.00000000"},
        }


def run_fake_gateway() -> tuple[FakeGatewayServer, str, threading.Thread]:
    server = FakeGatewayServer()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, url, thread
