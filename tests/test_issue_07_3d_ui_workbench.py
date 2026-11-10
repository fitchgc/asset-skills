from __future__ import annotations

import base64
import io
import json
import math
import os
import shutil
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

MODEL_DIRECTION_AZIMUTHS = {
    "down": 90,
    "down-right": 45,
    "right": 0,
    "up-right": 315,
    "up": 270,
    "up-left": 225,
    "left": 180,
    "down-left": 135,
    "front": 270,
    "back": 90,
}


def closest_square_atlas_grid(total_frames: int, frame_width: int, frame_height: int) -> tuple[int, int]:
    best_cols = math.ceil(math.sqrt(total_frames))
    best_score = float("inf")
    for cols in range(1, total_frames + 1):
        rows = math.ceil(total_frames / cols)
        w = cols * frame_width
        h = rows * frame_height
        diff = abs(w - h)
        if diff < best_score:
            best_score = diff
            best_cols = cols
    rows = math.ceil(total_frames / best_cols)
    return best_cols, rows


class Test3DUIWorkbench(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)

        # Create dummy glb file
        self.source_glb = Path(self.temp_dir) / "test.glb"
        self.source_glb.write_bytes(b"FAKE_GLB_DATA")

        self.gateway_server, self.gateway_url, self.gateway_thread = run_fake_gateway()
        self.client = AsynxClient(self.gateway_url, "asx-3d-key")
        self.static_dir = REPO_ROOT / "src" / "ui" / "3d"

    def tearDown(self) -> None:
        self.gateway_server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_STATE_PATH", None)
        os.environ.pop("ASYNX_CONFIG_PATH", None)

    def test_atlas_grid_and_azimuth_compensation(self) -> None:
        """Verify closest square atlas grid calculations and Tripo camera azimuth compensation."""
        # 1. Grid packing
        c, r = closest_square_atlas_grid(24, 256, 256)
        self.assertEqual((c, r), (5, 5))

        c, r = closest_square_atlas_grid(8, 256, 256)
        self.assertEqual((c, r), (3, 3))

        c, r = closest_square_atlas_grid(12, 320, 180)
        self.assertEqual((c, r), (3, 4))

        # 2. Camera azimuth angles
        dirs = ["down-left", "down", "down-right", "left", "right", "up-left", "up", "up-right"]
        azimuths = [MODEL_DIRECTION_AZIMUTHS[d] for d in dirs]
        self.assertEqual(azimuths, [135, 90, 45, 180, 0, 225, 270, 315])
        self.assertEqual(MODEL_DIRECTION_AZIMUTHS["front"], 270)
        self.assertEqual(MODEL_DIRECTION_AZIMUTHS["back"], 90)

    def test_3d_render_sequence_submission(self) -> None:
        """Submitting multi-angle 3D atlas renders and writes atlas.png + manifest.json."""
        out_dir = Path(self.temp_dir) / "rendered_3d_atlas"
        server = WorkbenchServer(
            mode="model3d_render_sequence",
            source_path=str(self.source_glb),
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
            # Check static HTML
            with urllib.request.urlopen(f"{base_url}/") as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("Asynx 3D Workbench", html)

            # Submit rendered atlas
            atlas_img = PIL.Image.new("RGBA", (256, 256), (0, 100, 255, 255))
            buf = io.BytesIO()
            atlas_img.save(buf, format="PNG")
            atlas_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

            manifest_dict = {
                "frames": {
                    "frame-0000.png": {
                        "frame": {"x": 0, "y": 0, "w": 128, "h": 128},
                        "direction": "down",
                        "rotated": False,
                        "trimmed": False,
                        "duration": 0.1,
                    }
                },
                "meta": {
                    "app": "asynx-3d",
                    "version": "1.0",
                    "image": "atlas.png",
                    "view": "isometric_8way",
                },
            }

            req = urllib.request.Request(
                f"{base_url}/api/submit",
                data=json.dumps({"atlas": atlas_data_url, "manifest": manifest_dict}).encode("utf-8"),
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

            written_atlas = out_dir / "atlas.png"
            written_manifest = out_dir / "manifest.json"
            self.assertTrue(written_atlas.is_file())
            self.assertTrue(written_manifest.is_file())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
