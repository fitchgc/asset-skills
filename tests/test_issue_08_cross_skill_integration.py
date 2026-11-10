from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

import PIL.Image

from shared.asxlib.constants import VERSION
from shared.asxlib.state import connect_db, list_tasks
from tests.fake_gateway import run_fake_gateway
from tests.test_issue_03_audio_skill import make_test_wav
from tests.test_issue_04_video_skill import make_test_mp4
from tests.test_issue_05_3d_skill import make_test_glb


class TestLockstepReleaseAndCrossSkillIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import package_skill
        for mod in ("image", "audio", "video", "3d"):
            package_skill(mod)

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.agent_skills_dir = Path(self.temp_dir) / "agent_skills"
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"

        # Unpack all 4 zip packages into isolated agent skills directory
        dist_dir = REPO_ROOT / "dist"
        for skill_name in ("asynx-image", "asynx-audio", "asynx-video", "asynx-3d"):
            zip_path = dist_dir / f"{skill_name}.zip"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(self.agent_skills_dir)

        # Setup fake gateway
        self.server, self.gateway_url, self.thread = run_fake_gateway()
        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_API_KEY": "asx-cross-skill-key",
            "ASYNX_BASE_URL": self.gateway_url,
        }

        # Setup test fixtures for fake gateway responses
        self.sample_png = Path(self.temp_dir) / "sample.png"
        PIL.Image.new("RGBA", (100, 100), (255, 128, 0, 255)).save(self.sample_png)

        self.sample_wav = Path(self.temp_dir) / "sample.wav"
        make_test_wav(self.sample_wav)

        self.sample_mp4 = Path(self.temp_dir) / "sample.mp4"
        make_test_mp4(self.sample_mp4)

        self.sample_glb = Path(self.temp_dir) / "sample.glb"
        make_test_glb(self.sample_glb)

        self.server.asset_data["task_1:0"] = self.sample_png.read_bytes()
        self.server.asset_mimes["task_1:0"] = "image/png"
        self.server.asset_data["task_2:0"] = self.sample_mp4.read_bytes()
        self.server.asset_mimes["task_2:0"] = "video/mp4"
        self.server.asset_data["task_3:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_3:0"] = "model/gltf-binary"
        self.server.asset_data["task_4:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_4:0"] = "model/gltf-binary"
        self.server.asset_data["task_5:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_5:0"] = "model/gltf-binary"
        self.server.asset_data["task_6:0"] = self.sample_wav.read_bytes()
        self.server.asset_mimes["task_6:0"] = "audio/wav"

    def tearDown(self) -> None:
        self.server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_skill_cli(self, skill_name: str, *args: str) -> dict[str, Any]:
        asynx_bin = self.agent_skills_dir / skill_name / "scripts" / "asynx.py"
        proc = subprocess.run(
            [sys.executable, str(asynx_bin), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"[{skill_name}] stderr: {proc.stderr}\nstdout: {proc.stdout}")
        return json.loads(proc.stdout)

    def test_release_packages_and_manifest_integrity(self) -> None:
        """Lockstep release packaging produces 4 zip archives with verified SHA-256 manifests."""
        dist_dir = REPO_ROOT / "dist"
        skills = ("asynx-image", "asynx-audio", "asynx-video", "asynx-3d")

        for skill in skills:
            zip_file = dist_dir / f"{skill}.zip"
            manifest_file = dist_dir / f"{skill}-{VERSION}.json"

            self.assertTrue(zip_file.is_file(), f"Missing release archive: {zip_file}")
            self.assertTrue(manifest_file.is_file(), f"Missing manifest: {manifest_file}")

            # Verify sha256 in manifest matches actual archive
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            self.assertEqual(manifest["skill"], skill)
            self.assertEqual(manifest["version"], VERSION)

            hasher = hashlib.sha256()
            with zip_file.open("rb") as f:
                while chunk := f.read(64 * 1024):
                    hasher.update(chunk)

            self.assertEqual(manifest["sha256"], hasher.hexdigest())

    def test_end_to_end_cross_modal_game_asset_workflow(self) -> None:
        """Exercise full cross-skill pipeline across Image, Video, 3D, and Audio."""
        out_root = Path(self.temp_dir) / "output"

        # Step 1: asynx-image generates concept art
        img_res = self.run_skill_cli(
            "asynx-image", "generate", "--prompt", "cyberpunk warrior hero", "--output-dir", str(out_root / "image")
        )
        self.assertTrue(img_res["ok"])
        img_task_id = img_res["task_id"]
        self.assertIsNotNone(img_task_id)

        # Step 2: asynx-video chains --from-task img_task_id -> video -> spritesheet
        vid_res = self.run_skill_cli(
            "asynx-video", "generate", "--prompt", "warrior running", "--from-task", img_task_id, "--output-dir", str(out_root / "video")
        )
        self.assertTrue(vid_res["ok"])
        vid_path = vid_res["files"][0]

        sheet_res = self.run_skill_cli(
            "asynx-video", "video", "spritesheet", vid_path, "--output-dir", str(out_root / "spritesheet")
        )
        self.assertTrue(sheet_res["ok"])
        self.assertTrue(Path(sheet_res["spritesheet"]["atlas_path"]).is_file())
        self.assertTrue(Path(sheet_res["spritesheet"]["manifest_path"]).is_file())

        # Step 3: asynx-3d chains --from-task img_task_id -> generate 3d -> rig -> animate -> info
        m3d_res = self.run_skill_cli(
            "asynx-3d", "generate", "--from-task", img_task_id, "--output-format", "glb", "--output-dir", str(out_root / "3d")
        )
        self.assertTrue(m3d_res["ok"])
        m3d_task_id = m3d_res["task_id"]

        rig_res = self.run_skill_cli(
            "asynx-3d", "rig", "--from-task", m3d_task_id, "--rig-type", "biped", "--output-dir", str(out_root / "3d")
        )
        self.assertTrue(rig_res["ok"])
        rig_task_id = rig_res["task_id"]

        anim_res = self.run_skill_cli(
            "asynx-3d", "animate", "--from-task", rig_task_id, "--animation", "run", "--output-dir", str(out_root / "3d")
        )
        self.assertTrue(anim_res["ok"])
        anim_glb_path = anim_res["files"][0]

        info_3d = self.run_skill_cli(
            "asynx-3d", "model3d", "info", anim_glb_path
        )
        self.assertTrue(info_3d["ok"])
        self.assertEqual(info_3d["model"]["format"], "glb")

        # Step 4: asynx-audio generates companion SFX and normalizes it
        sfx_res = self.run_skill_cli(
            "asynx-audio", "sfx", "--prompt", "cyberpunk laser fire", "--output-dir", str(out_root / "audio")
        )
        self.assertTrue(sfx_res["ok"])
        sfx_path = sfx_res["files"][0]

        norm_res = self.run_skill_cli(
            "asynx-audio", "audio", "normalize", sfx_path, "--output", str(out_root / "audio" / "normalized.wav")
        )
        self.assertTrue(norm_res["ok"])
        self.assertTrue(Path(norm_res["audio"]["path"]).is_file())

        # Verify all 4 modalities recorded in shared SQLite state ledger
        conn = connect_db(self.state_db)
        img_tasks = list_tasks(conn, modality="image")
        vid_tasks = list_tasks(conn, modality="video")
        m3d_tasks = list_tasks(conn, modality="3d")
        aud_tasks = list_tasks(conn, modality="audio")

        self.assertEqual(len(img_tasks), 1)
        self.assertEqual(len(vid_tasks), 1)
        self.assertEqual(len(m3d_tasks), 3)  # generate, rig, animate
        self.assertEqual(len(aud_tasks), 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
