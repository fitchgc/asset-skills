"""Issue 03: generate → query → download works with no Pillow importable.

Every CLI call runs through ``tests/pil_blocker.py`` (``python -S`` plus a
``sys.meta_path`` blocker), so Pillow is unavailable even though the repository
still vendors it. The blocker logs attempted ``PIL`` imports.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.charge_args import with_consent  # noqa: E402
from tests.bridge_env import bridge_env
from tests.fake_gateway import run_fake_gateway  # noqa: E402

BLOCKER = REPO_ROOT / "tests" / "pil_blocker.py"


def png_bytes(width: int, height: int, *, alpha: bool = True) -> bytes:
    """Minimal valid PNG written without Pillow."""
    color_type, channels = (6, 4) if alpha else (2, 3)
    row = b"\x00" + b"\x80" * (width * channels)
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class PillowFreeCase(unittest.TestCase):
    skill = ""

    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill

        for modality in ("image", "audio", "video", "3d"):
            build_skill(modality)

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.pil_log = self.temp_dir / "pil-imports.log"
        self.server, url, _thread = run_fake_gateway()
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.temp_dir / "home"),
            "ASYNX_STATE_PATH": str(self.temp_dir / "state.db"),
            "ASYNX_CONFIG_PATH": str(self.temp_dir / "config.json"),
            **bridge_env(self, Path(self.temp_dir), url),
            "ASYNX_UPDATE_CHECK": "off",
            "ASX_PIL_IMPORT_LOG": str(self.pil_log),
        }

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def cli(self, skill: str, *args: str, expect_ok: bool = True) -> dict[str, Any]:
        script = REPO_ROOT / "skills" / skill / "scripts" / "asynx.py"
        proc = subprocess.run(
            [sys.executable, "-S", str(BLOCKER), str(script), *with_consent(args)],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.temp_dir,
        )
        self.assertTrue(proc.stdout.strip(), f"no JSON output; stderr: {proc.stderr}")
        payload = json.loads(proc.stdout)
        if expect_ok:
            self.assertEqual(proc.returncode, 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
            self.assertTrue(payload.get("ok"), proc.stdout)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse(payload.get("ok"), proc.stdout)
        return payload

    def pil_imports(self) -> list[str]:
        if not self.pil_log.is_file():
            return []
        return [line for line in self.pil_log.read_text(encoding="utf-8").splitlines() if line]

    def assert_dependency_missing(self, payload: dict[str, Any]) -> None:
        error = payload["error"]
        self.assertEqual(error["code"], "dependency_missing", payload)
        self.assertEqual(error["details"]["dependency"], "Pillow")
        self.assertIn("runtime prepare", error["details"]["prepare_command"])
        self.assertTrue(error["details"]["requires_user_consent"])
        # Validation failed before submission: nothing reached the gateway.
        self.assertEqual(self.server.submissions, [])

    def task_lifecycle(self, skill: str, submit: list[str]) -> list[str]:
        """submit (detached) → status → wait/download; returns downloaded files."""
        out_dir = self.temp_dir / "out"
        submitted = self.cli(skill, *submit, "--detach", "--output-dir", str(out_dir))
        task_id = submitted["task_id"]
        self.cli(skill, "status", task_id)
        waited = self.cli(skill, "wait", task_id, "--output-dir", str(out_dir))
        files = waited.get("files") or []
        self.assertTrue(files, waited)
        for value in files:
            self.assertTrue(Path(value).is_file(), value)
        self.assertEqual(len(self.server.submissions), 1, "download must never resubmit")
        return files


class TestCoreFlowsNeverImportPillow(PillowFreeCase):
    def test_image_text_generation_status_wait_download(self) -> None:
        self.server.asset_data["task_1:0"] = png_bytes(64, 32)
        self.task_lifecycle("plaiers-asynx-image-pro", ["generate", "--prompt", "a lantern"])
        self.assertEqual(self.pil_imports(), [])

    def test_image_remote_reference_needs_no_decoding(self) -> None:
        self.server.asset_data["task_1:0"] = png_bytes(8, 8)
        self.task_lifecycle(
            "plaiers-asynx-image-pro",
            ["generate", "--prompt", "a lantern", "--reference", "https://example.com/ref.png"],
        )
        self.assertEqual(self.pil_imports(), [])

    def test_audio_text_generation_status_wait_download(self) -> None:
        self.server.asset_data["task_1:0"] = b"RIFF\x24\x00\x00\x00WAVEfmt "
        self.server.asset_mimes["task_1:0"] = "audio/wav"
        self.task_lifecycle("plaiers-asynx-audio", ["sfx", "--prompt", "sword clash", "--duration", "1.0"])
        self.assertEqual(self.pil_imports(), [])

    def test_video_text_generation_status_wait_download(self) -> None:
        self.server.asset_data["task_1:0"] = b"\x00\x00\x00\x18ftypmp42"
        self.server.asset_mimes["task_1:0"] = "video/mp4"
        self.task_lifecycle("plaiers-asynx-video", ["generate", "--prompt", "neon street"])
        self.assertEqual(self.pil_imports(), [])

    def test_3d_text_generation_status_wait_download(self) -> None:
        self.server.asset_data["task_1:0"] = b"glTF\x02\x00\x00\x00\x0c\x00\x00\x00"
        self.server.asset_mimes["task_1:0"] = "model/gltf-binary"
        self.task_lifecycle("plaiers-asynx-3d", ["generate", "--prompt", "magic sword"])
        self.assertEqual(self.pil_imports(), [])

    def test_image_batch_pause_resume_and_header_metadata(self) -> None:
        self.server.asset_data["task_1:0"] = png_bytes(64, 32)
        self.server.asset_data["task_2:0"] = b"not an image"
        self.server.default_submit_status = "running"
        out_dir = self.temp_dir / "batch"
        created = self.cli(
            "plaiers-asynx-image-pro", "batch", "create", "--prompt", "tile", "--total", "2",
            "--aspect-ratio", "2:1", "--output-dir", str(out_dir),
        )
        batch_id = created["batch"]["id"]
        self.cli("plaiers-asynx-image-pro", "batch", "pause", batch_id)
        self.cli("plaiers-asynx-image-pro", "batch", "resume", batch_id)
        for task in self.server.tasks.values():
            task["status"] = "succeeded"
            task["result"] = {"assets": [{"index": 0, "download_url": None}]}
        batch = self.cli("plaiers-asynx-image-pro", "batch", "wait", batch_id, "--interval", "1", "--max-wait", "20")["batch"]
        self.assertEqual(batch["status"], "completed", batch)
        self.assertEqual(len(self.server.submissions), 2)
        files = batch["files"]
        self.assertEqual(len(files), 2)
        for value in files:
            self.assertTrue(Path(value).is_file(), value)

        images = {Path(item["path"]).name: item for item in batch["images"]}
        readable = [item for item in images.values() if "width" in item]
        self.assertEqual(len(readable), 1, batch["images"])
        self.assertEqual((readable[0]["width"], readable[0]["height"]), (64, 32))
        self.assertEqual(readable[0]["format"], "png")
        unreadable = [item for item in images.values() if "width" not in item]
        self.assertEqual(len(unreadable), 1)
        self.assertNotIn("format", unreadable[0])
        self.assertIn("size_bytes", unreadable[0])

        warnings = batch.get("warnings", [])
        notices = [w for w in warnings if w["code"] == "optional_metadata_unavailable"]
        self.assertTrue(notices, warnings)
        for notice in notices:
            self.assertIn("runtime prepare", notice["prepare_command"])
        self.assertFalse([w for w in warnings if w["code"] == "aspect_ratio_mismatch"], warnings)

        # Polling again neither resubmits nor re-downloads.
        self.cli("plaiers-asynx-image-pro", "batch", "status", batch_id, "--refresh")
        self.assertEqual(len(self.server.submissions), 2)
        # Only the optional metadata step tried Pillow.
        self.assertTrue(all(line.startswith("PIL") and "batch " in line for line in self.pil_imports()))

    def test_doctor_reports_optional_runtime_not_prepared(self) -> None:
        for skill in ("plaiers-asynx-image-pro", "plaiers-asynx-audio", "plaiers-asynx-video", "plaiers-asynx-3d"):
            with self.subTest(skill=skill):
                report = self.cli(skill, "doctor")
                runtime = report["optional_runtime"]
                self.assertEqual(runtime["status"], "not_prepared")
                self.assertIn("runtime prepare", runtime["prepare_command"])
                check = next(c for c in report["checks"] if c["name"] == "optional_runtime")
                self.assertTrue(check["ok"])
                self.assertEqual(check["status"], "not_prepared")
                self.assertFalse([i for i in report["issues"] if "pillow" in i["code"].lower()])


class TestInputsNeedingPillowFailBeforeSubmission(PillowFreeCase):
    def setUp(self) -> None:
        super().setUp()
        self.ref_png = self.temp_dir / "ref.png"
        self.ref_png.write_bytes(png_bytes(32, 32))
        self.mask_png = self.temp_dir / "mask.png"
        self.mask_png.write_bytes(png_bytes(32, 32))

    def test_image_generate_with_local_reference(self) -> None:
        payload = self.cli(
            "plaiers-asynx-image-pro", "generate", "--prompt", "x", "--reference", str(self.ref_png),
            expect_ok=False,
        )
        self.assert_dependency_missing(payload)

    def test_image_edit_with_mask(self) -> None:
        payload = self.cli(
            "plaiers-asynx-image-pro", "edit", "--prompt", "x", "--image", "https://example.com/a.png",
            "--mask", str(self.mask_png), expect_ok=False,
        )
        self.assert_dependency_missing(payload)

    def test_image_batch_with_local_reference(self) -> None:
        payload = self.cli(
            "plaiers-asynx-image-pro", "batch", "create", "--operation", "edit", "--prompt", "x",
            "--image", str(self.ref_png), expect_ok=False,
        )
        self.assert_dependency_missing(payload)

    def test_video_generate_with_local_image(self) -> None:
        payload = self.cli(
            "plaiers-asynx-video", "generate", "--prompt", "x", "--image", str(self.ref_png),
            expect_ok=False,
        )
        self.assert_dependency_missing(payload)

    def test_3d_generate_with_local_image(self) -> None:
        payload = self.cli("plaiers-asynx-3d", "generate", "--image", str(self.ref_png), expect_ok=False)
        self.assert_dependency_missing(payload)

    def test_local_image_processing(self) -> None:
        payload = self.cli(
            "plaiers-asynx-image-pro", "image", "resize", str(self.ref_png),
            "--output", str(self.temp_dir / "o.png"), "--width", "8", "--height", "8",
            expect_ok=False,
        )
        self.assertEqual(payload["error"]["code"], "dependency_missing")


if __name__ == "__main__":
    unittest.main()
