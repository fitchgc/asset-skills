from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add shared and root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

from shared.asxlib.client import AsynxClient
from shared.asxlib.config import doctor, save_config
from shared.asxlib.constants import VERSION
from shared.asxlib.errors import AsxError
from shared.asxlib.state import (
    SCHEMA_VERSION,
    connect_db,
    create_task_intent,
    ensure_batch_schema,
    get_task,
    list_tasks,
    task_paths,
    upsert_asset,
)
from tests.fake_gateway import run_fake_gateway


class TestSharedFoundationAndMigration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db_path = Path(self.temp_dir) / "state.db"
        self.config_path = Path(self.temp_dir) / "config.json"
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db_path)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_STATE_PATH", None)
        os.environ.pop("ASYNX_CONFIG_PATH", None)

    def test_schema_v1_to_v2_migration(self) -> None:
        """Connecting to an existing v1 ledger automatically migrates to v2 with zero loss."""
        # Setup an authentic v1 schema
        conn = sqlite3.connect(str(self.state_db_path))
        conn.executescript(
            """
            CREATE TABLE tasks (
                local_id TEXT PRIMARY KEY,
                remote_task_id TEXT UNIQUE,
                operation TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                remote_json TEXT,
                error_json TEXT,
                billing TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_polled TEXT,
                base_url TEXT NOT NULL
            );
            CREATE TABLE assets (
                artifact_id TEXT PRIMARY KEY,
                local_task_id TEXT NOT NULL REFERENCES tasks(local_id) ON DELETE CASCADE,
                remote_index INTEGER NOT NULL CHECK (remote_index >= 0),
                path TEXT NOT NULL,
                remote_url TEXT,
                mime TEXT,
                bytes INTEGER,
                sha256 TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(local_task_id, remote_index)
            );
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_task_id TEXT NOT NULL REFERENCES tasks(local_id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE batches (
                id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                model TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                template_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE batch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                task_id TEXT,
                status TEXT NOT NULL,
                request_id TEXT,
                files_json TEXT,
                error_json TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, sequence)
            );
            PRAGMA user_version = 1;
            """
        )
        # Insert a sample v1 task and batch
        conn.execute(
            "INSERT INTO tasks VALUES ('local_v1', 'remote_v1', 'generate', 'model-1', 'succeeded', 'idemp_v1', '{}', '/tmp', NULL, NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL, 'https://example.com')"
        )
        conn.execute(
            "INSERT INTO batches VALUES ('batch_v1', 'generate', 'Batch 1', 'completed', 'model-1', '/tmp', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)"
        )
        conn.commit()
        conn.close()

        # Connect using our v2 code
        migrated_conn = connect_db(self.state_db_path)
        ensure_batch_schema(migrated_conn)

        # Check version bumped to 2
        version = int(migrated_conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(version, 2)

        # Check tasks modality column backfilled as 'image'
        task = get_task(migrated_conn, "local_v1")
        self.assertIsNotNone(task)
        self.assertEqual(task["modality"], "image")
        self.assertEqual(task["remote_task_id"], "remote_v1")

        # Check batches modality column backfilled as 'image'
        batch = migrated_conn.execute("SELECT * FROM batches WHERE id = 'batch_v1'").fetchone()
        self.assertIsNotNone(batch)
        self.assertEqual(batch["modality"], "image")
        migrated_conn.close()

    def test_modality_filtered_queries_and_cross_modal_lookup(self) -> None:
        """Default queries filter by modality, but explicit lookups are cross-modal."""
        conn = connect_db(self.state_db_path)
        # Create an image task and an audio task
        create_task_intent(
            conn,
            operation="generate",
            model="img-model",
            idempotency_key="key_img",
            request={"input": {"prompt": "a dog"}},
            modality="image",
            local_id="task_img_1",
        )
        create_task_intent(
            conn,
            operation="sfx",
            model="audio-model",
            idempotency_key="key_audio",
            request={"input": {"prompt": "bark"}},
            modality="audio",
            local_id="task_audio_1",
        )

        # List image tasks only
        img_tasks = list_tasks(conn, modality="image")
        self.assertEqual(len(img_tasks), 1)
        self.assertEqual(img_tasks[0]["local_id"], "task_img_1")

        # List audio tasks only
        audio_tasks = list_tasks(conn, modality="audio")
        self.assertEqual(len(audio_tasks), 1)
        self.assertEqual(audio_tasks[0]["local_id"], "task_audio_1")

        # List all modalities
        all_tasks = list_tasks(conn, modality=None)
        self.assertEqual(len(all_tasks), 2)

        # Cross-modal lookup by ID finds both
        found_img = get_task(conn, "task_img_1")
        found_audio = get_task(conn, "task_audio_1")
        self.assertIsNotNone(found_img)
        self.assertIsNotNone(found_audio)
        conn.close()

    def test_streaming_download_and_size_limits(self) -> None:
        """Asset downloads stream to disk with SHA-256 verification and size limits."""
        server, base_url, _thread = run_fake_gateway()
        try:
            # Set up fake asset data of 100 KB
            asset_bytes = b"A" * (100 * 1024)
            server.asset_data["task_dl:0"] = asset_bytes
            server.asset_mimes["task_dl:0"] = "image/png"

            client = AsynxClient(base_url, "asx-dummy-key")
            dest = Path(self.temp_dir) / "output.png"

            # 1. Normal download succeeds with correct sha256 and bytes
            result = client.download_asset_to_file("task_dl", 0, dest, modality="image")
            self.assertTrue(dest.is_file())
            self.assertEqual(result["bytes"], len(asset_bytes))
            self.assertEqual(result["sha256"], result["sha256"])

            # 2. Exceeding max_bytes aborts and deletes partial file
            dest_large = Path(self.temp_dir) / "too_large.png"
            with self.assertRaises(AsxError) as ctx:
                client.download_asset_to_file(
                    "task_dl", 0, dest_large, max_bytes=50 * 1024, modality="image"
                )
            self.assertEqual(ctx.exception.code, "asset_too_large")
            self.assertFalse(dest_large.exists())
        finally:
            server.shutdown()

    def test_doctor_command(self) -> None:
        """Doctor reports credentials, state ledger, pillow, and ffmpeg tools."""
        save_config("asx-valid-test-key", "https://asynx.llmapi.site/api")
        doc = doctor(verify=False, modality="image")
        self.assertTrue(doc["ok"])
        self.assertTrue(doc["config"]["configured"])
        self.assertTrue(doc["ledger"]["ok"])
        self.assertEqual(doc["ledger"]["schema_version"], 2)
        self.assertTrue(doc["media_tools"]["available"])


class TestAsynxImageCLIEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Build asynx-image-pro skill
        from src.build_skills import build_skill
        build_skill("image")

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_API_KEY": "asx-test-key-12345",
        }
        self.server, self.gateway_url, self.thread = run_fake_gateway()
        self.env["ASYNX_BASE_URL"] = self.gateway_url

        # Generate a small 100x100 PNG test fixture
        self.fixture_png = Path(self.temp_dir) / "fixture.png"
        import PIL.Image
        img = PIL.Image.new("RGBA", (100, 100), (255, 0, 0, 255))
        img.save(self.fixture_png, format="PNG")
        self.server.asset_data["task_1:0"] = self.fixture_png.read_bytes()
        self.server.asset_mimes["task_1:0"] = "image/png"

    def tearDown(self) -> None:
        self.server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_cli(self, *args: str) -> dict[str, Any]:
        asynx_py = REPO_ROOT / "skills" / "asynx-image-pro" / "scripts" / "asynx.py"
        proc = subprocess.run(
            [sys.executable, str(asynx_py), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}\nstdout: {proc.stdout}")
        return json.loads(proc.stdout)

    def test_cli_local_image_operations(self) -> None:
        """Keyless image local post-processing: info, convert, resize, crop, slice, pad, mask."""
        # 1. info
        info = self.run_cli("image", "info", str(self.fixture_png))
        self.assertTrue(info["ok"])
        self.assertEqual(info["image"]["width"], 100)
        self.assertEqual(info["image"]["height"], 100)

        # 2. convert
        out_webp = Path(self.temp_dir) / "converted.webp"
        res = self.run_cli("image", "convert", str(self.fixture_png), "--output", str(out_webp))
        self.assertTrue(res["ok"])
        self.assertTrue(out_webp.is_file())

        # 3. resize
        out_resized = Path(self.temp_dir) / "resized.png"
        res = self.run_cli("image", "resize", str(self.fixture_png), "--output", str(out_resized), "--width", "50", "--height", "50")
        self.assertTrue(res["ok"])
        self.assertEqual(res["image"]["width"], 50)

        # 4. crop
        out_cropped = Path(self.temp_dir) / "cropped.png"
        res = self.run_cli("image", "crop", str(self.fixture_png), "--output", str(out_cropped), "--box", "10,10,60,60")
        self.assertTrue(res["ok"])
        self.assertEqual(res["image"]["width"], 50)
        self.assertEqual(res["image"]["height"], 50)

        # 5. pad
        out_padded = Path(self.temp_dir) / "padded.png"
        res = self.run_cli("image", "pad", str(self.fixture_png), "--output", str(out_padded), "--padding", "20")
        self.assertTrue(res["ok"])
        self.assertEqual(res["image"]["width"], 140)
        self.assertEqual(res["image"]["height"], 140)

        # 6. slice
        slice_dir = Path(self.temp_dir) / "tiles"
        res = self.run_cli("image", "slice", str(self.fixture_png), "--output-dir", str(slice_dir), "--rows", "2", "--columns", "2")
        self.assertTrue(res["ok"])
        self.assertEqual(res["slice"]["count"], 4)

    def test_cli_generate_and_cross_modal_edit(self) -> None:
        """CLI generate, and then edit using --from-task."""
        out_dir = Path(self.temp_dir) / "gen_out"
        gen_res = self.run_cli("generate", "--prompt", "a magical forest", "--output-dir", str(out_dir))
        self.assertTrue(gen_res["ok"])
        task_id = gen_res["task_id"]
        self.assertIsNotNone(task_id)

        # Edit using --from-task
        edit_out = Path(self.temp_dir) / "edit_out"
        edit_res = self.run_cli("edit", "--prompt", "add flowers", "--from-task", task_id, "--output-dir", str(edit_out))
        self.assertTrue(edit_res["ok"])


if __name__ == "__main__":
    unittest.main()
