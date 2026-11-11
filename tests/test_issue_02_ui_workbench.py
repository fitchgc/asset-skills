from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

import PIL.Image

from shared.asxlib.client import AsynxClient
from shared.asxlib.ui_server import WorkbenchServer
from tests.fake_gateway import run_fake_gateway


class TestLocalUIWorkbench(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)

        # Create sample PNG image
        self.source_png = Path(self.temp_dir) / "source.png"
        img = PIL.Image.new("RGBA", (100, 100), (0, 128, 255, 255))
        img.save(self.source_png, format="PNG")

        self.gateway_server, self.gateway_url, self.gateway_thread = run_fake_gateway()
        self.client = AsynxClient(self.gateway_url, "asx-test-key")
        self.static_dir = REPO_ROOT / "src" / "ui" / "image"

    def tearDown(self) -> None:
        self.gateway_server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_STATE_PATH", None)
        os.environ.pop("ASYNX_CONFIG_PATH", None)

    def test_auth_and_static_serving(self) -> None:
        """UI server binds ephemeral port with token; rejects unauthenticated requests and serves static index."""
        server = WorkbenchServer(
            mode="tools",
            source_path=str(self.source_png),
            output_path=str(Path(self.temp_dir) / "out.png"),
            output_dir=self.temp_dir,
            static_dir=self.static_dir,
            client=self.client,
        )
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # 1. Unauthenticated request to /api/session is rejected with 401
            req = urllib.request.Request(f"{base_url}/api/session")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 401)

            # 2. Authenticated request to /api/session succeeds
            req_auth = urllib.request.Request(
                f"{base_url}/api/session",
                headers={"X-Asx-Session-Token": server.session_token},
            )
            with urllib.request.urlopen(req_auth) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["mode"], "tools")

            # 3. Static index.html is served
            with urllib.request.urlopen(f"{base_url}/") as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("Asynx Local UI Workbench", html)

            # 4. Source image streaming with token query param
            with urllib.request.urlopen(f"{base_url}/api/source?token={server.session_token}") as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "image/png")
                self.assertEqual(len(resp.read()), self.source_png.stat().st_size)
        finally:
            server.shutdown()
            server.server_close()

    def test_region_edit_submission(self) -> None:
        """In region-edit mode, submitting mask and annotations runs image.edit against gateway."""
        server = WorkbenchServer(
            mode="region_edit",
            source_path=str(self.source_png),
            output_path=None,
            output_dir=self.temp_dir,
            static_dir=self.static_dir,
            client=self.client,
        )
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # Fake image generation result on gateway
            self.gateway_server.asset_data["task_1:0"] = self.source_png.read_bytes()
            self.gateway_server.asset_mimes["task_1:0"] = "image/png"

            # Create dummy data URLs for mask and preview
            mask_png = PIL.Image.new("RGBA", (100, 100), (0, 0, 0, 0))
            for x in range(25, 75):
                for y in range(25, 75):
                    mask_png.putpixel((x, y), (255, 255, 255, 255))
            mask_buf = io.BytesIO()
            mask_png.save(mask_buf, format="PNG")
            mask_data_url = "data:image/png;base64," + base64.b64encode(mask_buf.getvalue()).decode()

            preview_png = PIL.Image.new("RGBA", (100, 100), (255, 0, 0, 255))
            preview_buf = io.BytesIO()
            preview_png.save(preview_buf, format="PNG")
            preview_data_url = "data:image/png;base64," + base64.b64encode(preview_buf.getvalue()).decode()

            submit_body = {
                "mask": mask_data_url,
                "annotation_preview": preview_data_url,
                "annotations": [
                    {"id": "mark_1", "type": "arrow", "instruction": "change sword to gold"},
                    {"id": "mark_2", "type": "rectangle", "instruction": "remove shield logo"},
                ],
                "prompt": "high fantasy armor",
                "model": "gpt-image-2.5-sunburst",
            }

            req = urllib.request.Request(
                f"{base_url}/api/submit",
                data=json.dumps(submit_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Asx-Session-Token": server.session_token,
                },
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                res = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res["ok"])

            # Verify prompt formatting received by fake gateway
            last_sub = self.gateway_server.submissions[-1]
            self.assertEqual(last_sub["task_type"], "image.edit")
            inp = last_sub["input"]
            self.assertIn("Image edit annotations:", inp["prompt"])
            self.assertIn("- change sword to gold", inp["prompt"])
            self.assertIn("- remove shield logo", inp["prompt"])
            # Verify inputs ordered as [source, annotation_preview]
            self.assertEqual(len(inp["images"]), 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_image_tools_and_annotate_submission(self) -> None:
        """In tools and annotate modes, submitting image writes file to disk without gateway."""
        out_path = Path(self.temp_dir) / "tool_output.png"
        server = WorkbenchServer(
            mode="image_tools",
            source_path=str(self.source_png),
            output_path=str(out_path),
            output_dir=self.temp_dir,
            static_dir=self.static_dir,
            client=self.client,
        )
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            tool_img = PIL.Image.new("RGBA", (64, 64), (0, 255, 0, 255))
            buf = io.BytesIO()
            tool_img.save(buf, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

            submit_body = {
                "image_data": data_url,
                "recipe": "inventory_icon",
            }
            req = urllib.request.Request(
                f"{base_url}/api/submit",
                data=json.dumps(submit_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Asx-Session-Token": server.session_token,
                },
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                res = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res["ok"])

            self.assertTrue(out_path.is_file())
            self.assertEqual(out_path.stat().st_size, len(buf.getvalue()))
        finally:
            server.shutdown()
            server.server_close()

    def test_region_edit_workbench_tool_save(self) -> None:
        """Switching to tools tab inside region_edit mode saves image locally without calling gateway."""
        server = WorkbenchServer(
            mode="region_edit",
            source_path=str(self.source_png),
            output_path=None,
            output_dir=self.temp_dir,
            static_dir=self.static_dir,
            client=self.client,
        )
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            tool_img = PIL.Image.new("RGBA", (80, 80), (255, 255, 0, 255))
            buf = io.BytesIO()
            tool_img.save(buf, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

            submit_body = {
                "mode": "tools",
                "image_data": data_url,
                "recipe": "custom",
                "keep_alive": True,
            }
            req = urllib.request.Request(
                f"{base_url}/api/submit",
                data=json.dumps(submit_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Asx-Session-Token": server.session_token,
                },
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                res = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res["ok"])
                self.assertIn("files", res["result"])
                saved_file = Path(res["result"]["files"][0])
                self.assertTrue(saved_file.is_file())

            # Verify server is kept alive (shutdown_event not set)
            time.sleep(1.8)
            self.assertFalse(server.shutdown_event.is_set())
        finally:
            server.shutdown()
            server.server_close()

    def test_region_edit_without_annotation_preview(self) -> None:
        """Submitting region_edit without annotation_preview does not throw missing file error."""
        server = WorkbenchServer(
            mode="region_edit",
            source_path=str(self.source_png),
            output_path=None,
            output_dir=self.temp_dir,
            static_dir=self.static_dir,
            client=self.client,
        )
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            self.gateway_server.asset_data["task_2:0"] = self.source_png.read_bytes()
            self.gateway_server.asset_mimes["task_2:0"] = "image/png"

            mask_img = PIL.Image.new("RGBA", (100, 100), (0, 0, 0, 0))
            for x in range(25, 75):
                for y in range(25, 75):
                    mask_img.putpixel((x, y), (255, 255, 255, 255))
            mbuf = io.BytesIO()
            mask_img.save(mbuf, format="PNG")
            mask_data_url = "data:image/png;base64," + base64.b64encode(mbuf.getvalue()).decode()

            region_body = {
                "mask": mask_data_url,
                "prompt": "only brush mask without preview",
                "model": "gpt-image-2.5-sunburst",
            }
            req2 = urllib.request.Request(
                f"{base_url}/api/submit",
                data=json.dumps(region_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Asx-Session-Token": server.session_token,
                },
                method="POST",
            )
            with urllib.request.urlopen(req2) as resp:
                self.assertEqual(resp.status, 200)
                res2 = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res2["ok"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
