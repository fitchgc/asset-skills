from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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


class TestVideoUIWorkbench(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)

        # Create dummy mp4 file
        self.source_mp4 = Path(self.temp_dir) / "test.mp4"
        self.source_mp4.write_bytes(b"FAKE_MP4_HEADER_DATA")

        self.gateway_server, self.gateway_url, self.gateway_thread = run_fake_gateway()
        self.client = AsynxClient(self.gateway_url, "asx-video-key")
        self.static_dir = REPO_ROOT / "src" / "ui" / "video"

    def tearDown(self) -> None:
        self.gateway_server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_STATE_PATH", None)
        os.environ.pop("ASYNX_CONFIG_PATH", None)

    def test_frame_sequence_extractor_and_previewer_loopback(self) -> None:
        """Video frame sequence workbench serves UI, accepts atlas submission and writes atlas.png + manifest.json."""
        out_dir = Path(self.temp_dir) / "exported_atlas"
        server = WorkbenchServer(
            mode="video_frame_sequence",
            source_path=str(self.source_mp4),
            output_path=None,
            output_dir=str(out_dir),
            static_dir=self.static_dir,
            client=self.client,
        )
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # 1. Verify static index.html is served
            with urllib.request.urlopen(f"{base_url}/") as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("Asynx Video Workbench", html)

            # 2. Check /api/session
            req_session = urllib.request.Request(
                f"{base_url}/api/session",
                headers={"X-Asx-Session-Token": server.session_token},
            )
            with urllib.request.urlopen(req_session) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["mode"], "video_frame_sequence")

            # 3. Submit atlas and manifest
            atlas_img = PIL.Image.new("RGBA", (128, 128), (255, 0, 0, 255))
            buf = io.BytesIO()
            atlas_img.save(buf, format="PNG")
            atlas_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

            manifest_dict = {
                "frames": {
                    "frame-0000.png": {
                        "frame": {"x": 0, "y": 0, "w": 64, "h": 64},
                        "rotated": False,
                        "trimmed": False,
                        "sourceSize": {"w": 64, "h": 64},
                        "duration": 0.1,
                    }
                },
                "meta": {
                    "app": "asynx-video",
                    "version": "1.0",
                    "image": "atlas.png",
                    "fps": 10,
                },
            }

            submit_payload = {
                "atlas": atlas_data_url,
                "manifest": manifest_dict,
                "metadata": {"fps": 10, "frame_count": 1},
            }

            req_sub = urllib.request.Request(
                f"{base_url}/api/submit",
                data=json.dumps(submit_payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Asx-Session-Token": server.session_token,
                },
                method="POST",
            )
            with urllib.request.urlopen(req_sub) as resp:
                self.assertEqual(resp.status, 200)
                res = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res["ok"])

            # Verify written files on disk
            written_atlas = out_dir / "atlas.png"
            written_manifest = out_dir / "manifest.json"
            self.assertTrue(written_atlas.is_file())
            self.assertTrue(written_manifest.is_file())
            self.assertGreater(written_atlas.stat().st_size, 0)
            manifest_content = json.loads(written_manifest.read_text(encoding="utf-8"))
            self.assertIn("frame-0000.png", manifest_content["frames"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
