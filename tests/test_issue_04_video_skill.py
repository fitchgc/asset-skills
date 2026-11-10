from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

from shared.asxlib.state import connect_db, create_task_intent, list_tasks, upsert_asset
from tests.fake_gateway import run_fake_gateway


def make_test_mp4(path: Path) -> None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise unittest.SkipTest("ffmpeg not available")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=64x64:rate=10",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)


class TestAsynxVideoSkill(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        self.fixture_mp4 = Path(self.temp_dir) / "fixture.mp4"
        make_test_mp4(self.fixture_mp4)

        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_API_KEY": "asx-video-test-key",
        }
        self.server, self.gateway_url, self.thread = run_fake_gateway()
        self.env["ASYNX_BASE_URL"] = self.gateway_url
        self.asynx_bin = REPO_ROOT / "skills" / "asynx-video" / "scripts" / "asynx.py"

        # Provide fake video asset on gateway
        self.server.asset_data["task_1:0"] = self.fixture_mp4.read_bytes()
        self.server.asset_mimes["task_1:0"] = "video/mp4"

    def tearDown(self) -> None:
        self.server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_cli(self, *args: str) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, str(self.asynx_bin), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}\nstdout: {proc.stdout}")
        return json.loads(proc.stdout)

    def test_local_video_post_processing(self) -> None:
        """Keyless video commands: info, extract-frames, spritesheet, trim, convert, mux."""
        # 1. info
        info = self.run_cli("video", "info", str(self.fixture_mp4))
        self.assertTrue(info["ok"])
        v = info["video"]
        self.assertEqual(v["width"], 64)
        self.assertEqual(v["height"], 64)
        self.assertEqual(v["fps"], 10.0)

        # 2. extract-frames
        frames_dir = Path(self.temp_dir) / "frames"
        ext = self.run_cli("video", "extract-frames", str(self.fixture_mp4), "--output-dir", str(frames_dir))
        self.assertTrue(ext["ok"])
        self.assertGreaterEqual(ext["frames"]["frame_count"], 10)

        # 3. spritesheet
        atlas_dir = Path(self.temp_dir) / "atlas_out"
        sheet = self.run_cli("video", "spritesheet", str(self.fixture_mp4), "--output-dir", str(atlas_dir), "--fps", "10")
        self.assertTrue(sheet["ok"])
        self.assertTrue(Path(sheet["spritesheet"]["atlas_path"]).is_file())
        self.assertTrue(Path(sheet["spritesheet"]["manifest_path"]).is_file())

        # 4. trim
        trimmed_mp4 = Path(self.temp_dir) / "trimmed.mp4"
        trim = self.run_cli("video", "trim", str(self.fixture_mp4), "--output", str(trimmed_mp4), "--duration", "0.5")
        self.assertTrue(trim["ok"])
        self.assertTrue(trimmed_mp4.is_file())

        # 5. convert to gif
        out_gif = Path(self.temp_dir) / "out.gif"
        conv = self.run_cli("video", "convert", str(self.fixture_mp4), "--output", str(out_gif), "--format", "gif")
        self.assertTrue(conv["ok"])
        self.assertTrue(out_gif.is_file())

    def test_cloud_video_generation_and_cross_modal(self) -> None:
        """Cloud generation of text-to-video and cross-modal image-to-video via --from-task."""
        out_dir = Path(self.temp_dir) / "video_out"

        # 1. Text to video
        gen_res = self.run_cli("generate", "--prompt", "cyberpunk street", "--duration", "5.0", "--output-dir", str(out_dir))
        self.assertTrue(gen_res["ok"])
        self.assertEqual(gen_res["modality"], "video")

        conn = connect_db(self.state_db)
        tasks = list_tasks(conn, modality="video")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["modality"], "video")

        # 2. Create an image task in the ledger to simulate asynx-image output
        img_file = Path(self.temp_dir) / "concept.png"
        import PIL.Image
        PIL.Image.new("RGBA", (100, 100), (255, 100, 0, 255)).save(img_file)

        img_task = create_task_intent(
            conn,
            operation="generate",
            model="img-model",
            idempotency_key="concept_key",
            request={"input": {"prompt": "hero concept"}},
            modality="image",
            local_id="task_img_concept",
        )
        upsert_asset(conn, "task_img_concept", 0, path=str(img_file), state="available")
        conn.close()

        # 3. Cross-modal Image-to-Video using --from-task
        self.server.asset_data["task_2:0"] = self.fixture_mp4.read_bytes()
        cross_res = self.run_cli("generate", "--prompt", "animate hero", "--from-task", "task_img_concept", "--output-dir", str(out_dir))
        self.assertTrue(cross_res["ok"])
        self.assertEqual(cross_res["modality"], "video")

        # Verify submission sent first_frame input
        last_sub = self.server.submissions[-1]
        inp = last_sub["input"]
        first_frame = next((i for i in inp.get("inputs", []) if i["role"] == "first_frame"), None)
        self.assertIsNotNone(first_frame)


if __name__ == "__main__":
    unittest.main()
