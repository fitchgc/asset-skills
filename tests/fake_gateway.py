from __future__ import annotations

import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse


class FakeGatewayHandler(BaseHTTPRequestHandler):
    models_response = [
        {
            "name": "gpt-image-2.5-sunburst",
            "task_types": ["image.generate", "image.edit"],
            "capabilities": ["text_to_image", "image_to_image", "inpainting"],
        },
        {
            "name": "suno-v3",
            "task_types": ["audio.music"],
            "capabilities": ["text_to_music"],
        },
        {
            "name": "elevenlabs-v2",
            "task_types": ["audio.speech"],
            "capabilities": ["text_to_speech"],
        },
        {
            "name": "audiocraft-sfx",
            "task_types": ["audio.sfx"],
            "capabilities": ["text_to_sfx"],
        },
        {
            "name": "kling-video-v1",
            "task_types": ["video.generate", "video.edit"],
            "capabilities": ["text_to_video", "image_to_video"],
        },
        {
            "name": "tripo-3d-v2",
            "task_types": ["3d.generate", "3d.rig_check", "3d.rig", "3d.retarget", "3d.texture", "3d.convert"],
            "capabilities": ["text_to_3d", "image_to_3d"],
        },
    ]

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress server logging during tests

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/v1/tasks/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Request-Id", "req-models-123")
            self.end_headers()
            self.wfile.write(json.dumps({"models": self.models_response}).encode("utf-8"))
            return

        if path.startswith("/v1/tasks/") and "/assets/" in path:
            parts = path.split("/")
            task_id = parts[3]
            index = int(parts[5])
            asset_data = self.server.asset_data.get(f"{task_id}:{index}", b"FAKE_ASSET_CONTENT")
            content_type = self.server.asset_mimes.get(f"{task_id}:{index}", "image/png")

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(asset_data)))
            self.end_headers()
            self.wfile.write(asset_data)
            return

        if path.startswith("/v1/tasks/"):
            task_id = path.split("/")[-1]
            task = self.server.tasks.get(task_id, {
                "id": task_id,
                "status": "succeeded",
                "result": {"assets": [{"index": 0, "download_url": None}]},
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Request-Id", f"req-task-{task_id}")
            self.end_headers()
            self.wfile.write(json.dumps(task).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        if path.startswith("/v1/tasks/") and path.endswith("/cancel"):
            task_id = path.split("/")[3]
            task = self.server.tasks.get(task_id, {"id": task_id, "status": "canceled"})
            task["status"] = "canceled"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(task).encode("utf-8"))
            return

        if path == "/v1/tasks":
            task_id = f"task_{len(self.server.tasks) + 1}"
            task_type = body.get("task_type", "image.generate")
            task = {
                "id": task_id,
                "task_type": task_type,
                "model": body.get("model", "default-model"),
                "status": "succeeded",
                "input": body.get("input", {}),
                "result": {
                    "assets": [
                        {
                            "index": 0,
                            "download_url": None,
                        }
                    ]
                },
            }
            self.server.tasks[task_id] = task
            self.server.submissions.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Request-Id", f"req-submit-{task_id}")
            self.end_headers()
            self.wfile.write(json.dumps(task).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


class FakeGatewayServer(HTTPServer):
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), FakeGatewayHandler)
        self.tasks: dict[str, dict[str, Any]] = {}
        self.asset_data: dict[str, bytes] = {}
        self.asset_mimes: dict[str, str] = {}
        self.submissions: list[dict[str, Any]] = []


def run_fake_gateway() -> tuple[FakeGatewayServer, str, threading.Thread]:
    server = FakeGatewayServer()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, url, thread
