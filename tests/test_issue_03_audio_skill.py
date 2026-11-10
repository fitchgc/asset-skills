from __future__ import annotations

import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))

from shared.asxlib.state import connect_db, get_task, list_tasks
from tests.fake_gateway import run_fake_gateway


def make_test_wav(path: Path, duration_seconds: float = 1.0, freq: float = 440.0) -> None:
    sample_rate = 44100
    n_samples = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            value = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            frames.extend(struct.pack("<h", value))
        w.writeframes(frames)


class TestAsynxAudioSkill(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        self.fixture_wav = Path(self.temp_dir) / "test.wav"
        make_test_wav(self.fixture_wav, duration_seconds=1.0)

        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_API_KEY": "asx-audio-test-key",
        }
        self.server, self.gateway_url, self.thread = run_fake_gateway()
        self.env["ASYNX_BASE_URL"] = self.gateway_url
        self.asynx_bin = REPO_ROOT / "skills" / "asynx-audio" / "scripts" / "asynx.py"

        # Provide a fake audio asset on gateway
        self.server.asset_data["task_1:0"] = self.fixture_wav.read_bytes()
        self.server.asset_mimes["task_1:0"] = "audio/wav"

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

    def test_local_audio_post_processing(self) -> None:
        """Keyless audio commands: info, convert, trim, fade, normalize, concat."""
        # 1. info
        info = self.run_cli("audio", "info", str(self.fixture_wav))
        self.assertTrue(info["ok"])
        audio_data = info["audio"]
        self.assertEqual(audio_data["format"], "wav")
        self.assertEqual(audio_data["channels"], 1)
        self.assertEqual(audio_data["sample_rate"], 44100)
        self.assertAlmostEqual(audio_data["duration_seconds"], 1.0, places=1)
        self.assertGreater(audio_data["peak_amplitude"], 0.4)

        # 2. convert to mp3
        out_mp3 = Path(self.temp_dir) / "out.mp3"
        conv = self.run_cli("audio", "convert", str(self.fixture_wav), "--output", str(out_mp3))
        self.assertTrue(conv["ok"])
        self.assertTrue(out_mp3.is_file())

        # 3. trim
        out_trimmed = Path(self.temp_dir) / "trimmed.wav"
        trim = self.run_cli("audio", "trim", str(self.fixture_wav), "--output", str(out_trimmed), "--start", "0.2", "--duration", "0.5")
        self.assertTrue(trim["ok"])
        self.assertTrue(out_trimmed.is_file())
        self.assertAlmostEqual(trim["audio"]["duration_seconds"], 0.5, places=1)

        # 4. fade
        out_faded = Path(self.temp_dir) / "faded.wav"
        fade = self.run_cli("audio", "fade", str(self.fixture_wav), "--output", str(out_faded), "--fade-in", "0.2", "--fade-out", "0.2")
        self.assertTrue(fade["ok"])
        self.assertTrue(out_faded.is_file())

        # 5. normalize
        out_norm = Path(self.temp_dir) / "norm.wav"
        norm = self.run_cli("audio", "normalize", str(self.fixture_wav), "--output", str(out_norm), "--target-lufs", "-16.0")
        self.assertTrue(norm["ok"])
        self.assertTrue(out_norm.is_file())

        # 6. concat
        out_concat = Path(self.temp_dir) / "concat.wav"
        concat = self.run_cli("audio", "concat", str(self.fixture_wav), str(self.fixture_wav), "--output", str(out_concat))
        self.assertTrue(concat["ok"])
        self.assertTrue(out_concat.is_file())
        self.assertAlmostEqual(concat["audio"]["duration_seconds"], 2.0, places=1)

    def test_cloud_audio_generation(self) -> None:
        """Cloud generation of sfx, music, and speech records tasks under modality = 'audio'."""
        out_dir = Path(self.temp_dir) / "audio_out"

        # 1. sfx
        sfx_res = self.run_cli("sfx", "--prompt", "sword clash", "--duration", "2.0", "--output-dir", str(out_dir))
        self.assertTrue(sfx_res["ok"])
        self.assertEqual(sfx_res["modality"], "audio")

        # Verify task is recorded under modality = 'audio' in ledger
        conn = connect_db(self.state_db)
        tasks = list_tasks(conn, modality="audio")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["modality"], "audio")
        self.assertEqual(tasks[0]["operation"], "sfx")

        # 2. recent scoped to audio
        recent = self.run_cli("recent")
        self.assertTrue(recent["ok"])
        self.assertEqual(len(recent["artifacts"]), 1)
        self.assertEqual(recent["artifacts"][0]["modality"], "audio")

        # 3. speech with reference audio
        self.server.asset_data["task_2:0"] = self.fixture_wav.read_bytes()
        speech_res = self.run_cli("speech", "--text", "Advance to battle!", "--reference-audio", str(self.fixture_wav), "--output-dir", str(out_dir))
        self.assertTrue(speech_res["ok"])
        self.assertEqual(speech_res["modality"], "audio")
        conn.close()


if __name__ == "__main__":
    unittest.main()
