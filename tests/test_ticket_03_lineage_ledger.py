from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
from tests.charge_args import with_consent  # noqa: E402
from tests.interpreter_pillow import add_interpreter_pillow  # noqa: E402

add_interpreter_pillow()

import PIL.Image

from shared.asxlib.client import AsynxClient
from shared.asxlib.state import (
    SCHEMA_VERSION,
    compute_file_sha256,
    connect_db,
    create_task_intent,
    ensure_batch_schema,
    get_asset_by_path,
    get_asset_lineage,
    get_task,
    list_assets,
    list_lineage,
    record_artifact_with_lineage,
    upsert_asset,
)
from shared.asxlib.ui_server import WorkbenchServer
from tests.fake_gateway import run_fake_gateway
from tests.test_issue_03_audio_skill import make_test_wav
from tests.test_issue_04_video_skill import make_test_mp4
from tests.test_issue_05_3d_skill import make_test_glb


def _data_url_png(color: tuple[int, int, int, int] = (255, 0, 0, 255), size: tuple[int, int] = (64, 64)) -> str:
    img = PIL.Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _post_json(url: str, body: dict[str, Any], token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Asx-Session-Token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestSchemaV3MigrationAndHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "state.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fresh_v3_schema_and_indexes(self) -> None:
        conn = connect_db(self.db_path)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(version, 3)
            self.assertEqual(SCHEMA_VERSION, 3)

            asset_cols = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(assets)").fetchall()
            }
            self.assertTrue(
                {"category", "source_kind", "operation", "metadata_json"}.issubset(asset_cols)
            )
            lineage_cols = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(asset_lineage)").fetchall()
            }
            self.assertEqual(
                lineage_cols,
                {
                    "id",
                    "parent_artifact_id",
                    "parent_path",
                    "parent_sha256",
                    "child_artifact_id",
                    "child_path",
                    "child_sha256",
                    "operation",
                    "modality",
                    "task_id",
                    "created_at",
                },
            )
            indexes = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "ix_lineage_child",
                    "ix_lineage_parent_id",
                    "ix_lineage_parent_path",
                    "ix_lineage_parent_sha",
                }.issubset(indexes)
            )
        finally:
            conn.close()

    def test_v1_and_v2_to_v3_migration_preserves_data_and_is_idempotent(self) -> None:
        # 1. Create a v1 database with tasks, assets, batches
        conn = sqlite3.connect(str(self.db_path))
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
            PRAGMA user_version = 1;
            """
        )
        conn.execute(
            "INSERT INTO tasks VALUES ('t_v1', 'r_v1', 'generate', 'm1', 'succeeded', 'k_v1', '{}', '/tmp', NULL, NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL, 'https://example.com')"
        )
        conn.execute(
            "INSERT INTO assets VALUES ('art_v1', 't_v1', 0, '/tmp/v1.png', NULL, 'image/png', 123, 'abc123', 'available', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO batches VALUES ('b_v1', 'generate', 'B1', 'completed', 'm1', '/tmp', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)"
        )
        conn.commit()
        conn.close()

        # Connect twice to verify v1 -> v2 -> v3 migration AND idempotency
        for _ in range(2):
            migrated = connect_db(self.db_path)
            ensure_batch_schema(migrated)
            self.assertEqual(int(migrated.execute("PRAGMA user_version").fetchone()[0]), 3)
            t = get_task(migrated, "t_v1")
            self.assertIsNotNone(t)
            self.assertEqual(t["modality"], "image")
            assets = list_assets(migrated, "t_v1")
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0]["source_kind"], "cloud_task")
            self.assertEqual(assets[0]["sha256"], "abc123")
            batch_row = migrated.execute("SELECT * FROM batches WHERE id = 'b_v1'").fetchone()
            self.assertEqual(batch_row["modality"], "image")
            migrated.close()

        # 2. Create a separate v2 database and migrate v2 -> v3
        v2_db = Path(self.temp_dir) / "state_v2.db"
        conn2 = sqlite3.connect(str(v2_db))
        conn2.executescript(
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
                base_url TEXT NOT NULL,
                modality TEXT NOT NULL DEFAULT 'image'
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
            PRAGMA user_version = 2;
            """
        )
        conn2.execute(
            "INSERT INTO tasks VALUES ('t_v2', 'r_v2', 'sfx', 'aud-1', 'succeeded', 'k_v2', '{}', '/tmp', NULL, NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL, 'https://example.com', 'audio')"
        )
        conn2.execute(
            "INSERT INTO assets VALUES ('art_v2', 't_v2', 0, '/tmp/v2.wav', NULL, 'audio/wav', 456, 'def456', 'available', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn2.commit()
        conn2.close()

        for _ in range(2):
            m2 = connect_db(v2_db)
            self.assertEqual(int(m2.execute("PRAGMA user_version").fetchone()[0]), 3)
            assets2 = list_assets(m2, "t_v2")
            self.assertEqual(len(assets2), 1)
            self.assertEqual(assets2[0]["artifact_id"], "art_v2")
            self.assertEqual(assets2[0]["source_kind"], "cloud_task")
            m2.close()

    def test_record_artifact_with_lineage_single_multi_sha_and_idempotency(self) -> None:
        ext_parent = Path(self.temp_dir) / "external_ref.png"
        PIL.Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(ext_parent)
        ext_sha = compute_file_sha256(ext_parent)

        child1 = Path(self.temp_dir) / "child1.png"
        PIL.Image.new("RGBA", (32, 32), (40, 50, 60, 255)).save(child1)

        moved_copy = Path(self.temp_dir) / "moved_child1.png"
        shutil.copy2(child1, moved_copy)

        child2 = Path(self.temp_dir) / "child2.png"
        PIL.Image.new("RGBA", (32, 32), (70, 80, 90, 255)).save(child2)

        conn = connect_db(self.db_path)
        try:
            # 1. External parent + data: URL (ignored) + http URL (recorded)
            rec1 = record_artifact_with_lineage(
                conn,
                file_path=child1,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                parent_paths=[
                    ext_parent,
                    "data:image/png;base64,AAAA",
                    "https://cdn.example.com/style.png",
                ],
                task_id="task_remote_1",
            )
            self.assertEqual(rec1["category"], "image")
            self.assertEqual(rec1["source_kind"], "cloud_task")
            self.assertEqual(len(rec1["lineage_parents"]), 2)
            ext_edge = next(e for e in rec1["lineage_parents"] if e["parent_path"] == str(ext_parent.resolve()))
            self.assertIsNone(ext_edge["parent_artifact_id"])
            self.assertEqual(ext_edge["parent_sha256"], ext_sha)

            # 2. Idempotent re-record with same parent + child + operation does not duplicate edge
            local_id = rec1["local_task_id"]
            record_artifact_with_lineage(
                conn,
                file_path=child1,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                local_task_id=local_id,
                parent_paths=[ext_parent],
                task_id="task_remote_1",
            )
            edges_for_child1 = list_lineage(conn, child_artifact_id=rec1["artifact_id"])
            self.assertEqual(len(edges_for_child1), 2)

            # 3. Child2 derives from moved_copy (matched by SHA-256 to child1!) and ext_parent
            rec2 = record_artifact_with_lineage(
                conn,
                file_path=child2,
                modality="image",
                operation="contact-sheet",
                source_kind="local_process",
                parent_paths=[moved_copy, ext_parent],
            )
            self.assertEqual(len(rec2["lineage_parents"]), 2)
            matched_edge = next(
                e for e in rec2["lineage_parents"] if e["parent_path"] == str(moved_copy.resolve())
            )
            self.assertEqual(matched_edge["parent_artifact_id"], rec1["artifact_id"])

            # 4. Lineage DAG traversal
            dag = get_asset_lineage(conn, child2)
            self.assertEqual(dag["asset"]["artifact_id"], rec2["artifact_id"])
            self.assertEqual(len(dag["parents"]), 2)
            self.assertGreaterEqual(len(dag["ancestors"]), 3)

            up_from_child1 = get_asset_lineage(conn, rec1["artifact_id"])
            self.assertEqual(len(up_from_child1["children"]), 1)
            self.assertEqual(up_from_child1["children"][0]["child_artifact_id"], rec2["artifact_id"])
        finally:
            conn.close()


class TestKeylessLocalPostProcessingLineage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill
        for mod in ("image", "audio", "video", "3d"):
            build_skill(mod)

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        # Intentionally DO NOT configure ASYNX_PARTNER_TOKEN or config.json
        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(Path(self.temp_dir) / "nonexistent_config.json"),
        }
        self.env.pop("ASYNX_PARTNER_TOKEN", None)
        self.env.pop("ASYNX_BASE_URL", None)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_skill(self, skill_name: str, *args: str) -> dict[str, Any]:
        asynx_bin = REPO_ROOT / "skills" / skill_name / "scripts" / "asynx.py"
        proc = subprocess.run(
            [sys.executable, str(asynx_bin), *with_consent(args)],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"[{skill_name}] stderr={proc.stderr}\nstdout={proc.stdout}")
        return json.loads(proc.stdout)

    def test_keyless_local_image_lineage_all_operations(self) -> None:
        src1 = Path(self.temp_dir) / "src1.png"
        src2 = Path(self.temp_dir) / "src2.png"
        mask_png = Path(self.temp_dir) / "mask.png"
        bg_img = Path(self.temp_dir) / "bg.png"

        img1 = PIL.Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        img1.save(src1)
        PIL.Image.new("RGBA", (64, 64), (0, 255, 0, 255)).save(src2)
        PIL.Image.new("L", (64, 64), 128).save(mask_png)

        # Transparent border image for trim & alpha-bleed
        bordered = PIL.Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        for x in range(10, 30):
            for y in range(10, 30):
                bordered.putpixel((x, y), (200, 100, 50, 255))
        bordered.save(bg_img)

        out_conv = Path(self.temp_dir) / "out.webp"
        out_res = Path(self.temp_dir) / "resized.png"
        out_crop = Path(self.temp_dir) / "cropped.png"
        out_pad = Path(self.temp_dir) / "padded.png"
        out_trim = Path(self.temp_dir) / "trimmed.png"
        out_bleed = Path(self.temp_dir) / "bleed.png"
        out_rembg = Path(self.temp_dir) / "rembg.png"
        out_sheet = Path(self.temp_dir) / "sheet.png"
        out_masked = Path(self.temp_dir) / "masked.png"
        slice_dir = Path(self.temp_dir) / "slices"
        batch_in = Path(self.temp_dir) / "batch_in"
        batch_out = Path(self.temp_dir) / "batch_out"
        batch_in.mkdir()
        shutil.copy2(src1, batch_in / "a.png")
        shutil.copy2(src2, batch_in / "b.png")

        self.run_skill("asynx-image-pro", "image", "convert", str(src1), "--output", str(out_conv))
        self.run_skill("asynx-image-pro", "image", "resize", str(out_conv), "--output", str(out_res), "--width", "32", "--height", "32")
        self.run_skill("asynx-image-pro", "image", "crop", str(src1), "--output", str(out_crop), "--box", "0,0,32,32")
        self.run_skill("asynx-image-pro", "image", "pad", str(src1), "--output", str(out_pad), "--padding", "8")
        self.run_skill("asynx-image-pro", "image", "trim", str(bg_img), "--output", str(out_trim))
        self.run_skill("asynx-image-pro", "image", "alpha-bleed", str(bg_img), "--output", str(out_bleed))
        self.run_skill("asynx-image-pro", "image", "remove-background", str(bg_img), "--output", str(out_rembg))
        self.run_skill("asynx-image-pro", "image", "slice", str(src1), "--output-dir", str(slice_dir), "--rows", "2", "--columns", "2")
        self.run_skill("asynx-image-pro", "image", "contact-sheet", str(src1), str(src2), "--output", str(out_sheet))
        self.run_skill("asynx-image-pro", "image", "apply-mask", str(src1), "--mask", str(mask_png), "--output", str(out_masked))
        self.run_skill("asynx-image-pro", "image", "batch-convert", "--input-dir", str(batch_in), "--output-dir", str(batch_out), "--format", "webp")

        conn = connect_db(self.state_db)
        try:
            # Chain: src1 -> out_conv -> out_res
            res_dag = get_asset_lineage(conn, out_res)
            self.assertEqual(res_dag["asset"]["source_kind"], "local_process")
            self.assertEqual(res_dag["asset"]["operation"], "resize")
            self.assertEqual(len(res_dag["parents"]), 1)
            self.assertEqual(res_dag["parents"][0]["parent_path"], str(out_conv.resolve()))
            self.assertIsNotNone(res_dag["parents"][0]["parent_artifact_id"])
            self.assertEqual(len(res_dag["ancestors"]), 2)

            # Single-parent ops
            for out_p, op in (
                (out_crop, "crop"),
                (out_pad, "pad"),
                (out_trim, "trim"),
                (out_bleed, "alpha-bleed"),
                (out_rembg, "remove-background"),
            ):
                dag = get_asset_lineage(conn, out_p)
                self.assertEqual(dag["asset"]["operation"], op)
                self.assertEqual(len(dag["parents"]), 1)

            # 1-to-N slice
            tiles = sorted(slice_dir.glob("tile-*.png"))
            self.assertEqual(len(tiles), 4)
            for tile in tiles:
                dag = get_asset_lineage(conn, tile)
                self.assertEqual(dag["asset"]["operation"], "slice")
                self.assertEqual(dag["parents"][0]["parent_path"], str(src1.resolve()))

            # Multi-parent contact-sheet
            sheet_dag = get_asset_lineage(conn, out_sheet)
            self.assertEqual(sheet_dag["asset"]["operation"], "contact-sheet")
            self.assertEqual(
                {e["parent_path"] for e in sheet_dag["parents"]},
                {str(src1.resolve()), str(src2.resolve())},
            )

            # Multi-parent apply-mask
            masked_dag = get_asset_lineage(conn, out_masked)
            self.assertEqual(masked_dag["asset"]["operation"], "apply-mask")
            self.assertEqual(
                {e["parent_path"] for e in masked_dag["parents"]},
                {str(src1.resolve()), str(mask_png.resolve())},
            )

            # 1-to-1 batch-convert
            a_webp_dag = get_asset_lineage(conn, batch_out / "a.webp")
            b_webp_dag = get_asset_lineage(conn, batch_out / "b.webp")
            self.assertEqual(a_webp_dag["parents"][0]["parent_path"], str((batch_in / "a.png").resolve()))
            self.assertEqual(b_webp_dag["parents"][0]["parent_path"], str((batch_in / "b.png").resolve()))
        finally:
            conn.close()

    def test_keyless_local_audio_video_model3d_lineage(self) -> None:
        wav1 = Path(self.temp_dir) / "a1.wav"
        wav2 = Path(self.temp_dir) / "a2.wav"
        make_test_wav(wav1, duration_seconds=0.6, freq=440.0)
        make_test_wav(wav2, duration_seconds=0.6, freq=880.0)

        mp4 = Path(self.temp_dir) / "clip.mp4"
        make_test_mp4(mp4)

        glb = Path(self.temp_dir) / "mesh.glb"
        make_test_glb(glb)

        # Audio local ops
        a_mp3 = Path(self.temp_dir) / "a1.mp3"
        a_trim = Path(self.temp_dir) / "a1_trim.wav"
        a_fade = Path(self.temp_dir) / "a1_fade.wav"
        a_norm = Path(self.temp_dir) / "a1_norm.wav"
        a_concat = Path(self.temp_dir) / "concat.wav"

        self.run_skill("asynx-audio", "audio", "convert", str(wav1), "--output", str(a_mp3))
        self.run_skill("asynx-audio", "audio", "trim", str(wav1), "--output", str(a_trim), "--duration", "0.3")
        self.run_skill("asynx-audio", "audio", "fade", str(a_trim), "--output", str(a_fade), "--fade-in", "0.1")
        self.run_skill("asynx-audio", "audio", "normalize", str(a_fade), "--output", str(a_norm))
        self.run_skill("asynx-audio", "audio", "concat", str(wav1), str(wav2), "--output", str(a_concat))

        # Video local ops
        frames_dir = Path(self.temp_dir) / "frames"
        atlas_dir = Path(self.temp_dir) / "atlas"
        v_trim = Path(self.temp_dir) / "v_trim.mp4"
        v_gif = Path(self.temp_dir) / "v_conv.gif"
        v_mux = Path(self.temp_dir) / "v_mux.mp4"

        ext_res = self.run_skill("asynx-video", "video", "extract-frames", str(mp4), "--output-dir", str(frames_dir))
        sheet_res = self.run_skill("asynx-video", "video", "spritesheet", str(mp4), "--output-dir", str(atlas_dir), "--fps", "5")
        self.run_skill("asynx-video", "video", "trim", str(mp4), "--output", str(v_trim), "--duration", "0.5")
        self.run_skill("asynx-video", "video", "convert", str(mp4), "--output", str(v_gif), "--format", "gif")
        self.run_skill("asynx-video", "video", "mux", "--video", str(mp4), "--audio", str(a_norm), "--output", str(v_mux))

        # Model3d local extract-textures
        tex_dir = Path(self.temp_dir) / "textures"
        tex_res = self.run_skill("asynx-3d", "model3d", "extract-textures", str(glb), "--output-dir", str(tex_dir))

        conn = connect_db(self.state_db)
        try:
            # Audio chain + concat multi-parent
            norm_dag = get_asset_lineage(conn, a_norm)
            self.assertEqual(norm_dag["asset"]["operation"], "normalize")
            self.assertEqual(len(norm_dag["ancestors"]), 3)  # wav1 -> a_trim -> a_fade -> a_norm

            concat_dag = get_asset_lineage(conn, a_concat)
            self.assertEqual(
                {e["parent_path"] for e in concat_dag["parents"]},
                {str(wav1.resolve()), str(wav2.resolve())},
            )

            # Video extract-frames 1-to-N
            for frame_path in ext_res["frames"]["frames"]:
                f_dag = get_asset_lineage(conn, frame_path)
                self.assertEqual(f_dag["asset"]["category"], "image")
                self.assertEqual(f_dag["asset"]["operation"], "extract-frames")
                self.assertEqual(f_dag["parents"][0]["parent_path"], str(mp4.resolve()))

            # Video spritesheet
            atlas_path = sheet_res["spritesheet"]["atlas_path"]
            atlas_dag = get_asset_lineage(conn, atlas_path)
            self.assertEqual(atlas_dag["asset"]["category"], "frame_sequence")
            self.assertEqual(atlas_dag["asset"]["operation"], "spritesheet")
            self.assertEqual(atlas_dag["asset"]["metadata"]["manifest_path"], sheet_res["spritesheet"]["manifest_path"])
            self.assertEqual(atlas_dag["parents"][0]["parent_path"], str(mp4.resolve()))

            # Video mux multi-parent [mp4, a_norm]
            mux_dag = get_asset_lineage(conn, v_mux)
            self.assertEqual(mux_dag["asset"]["operation"], "mux")
            self.assertEqual(
                {e["parent_path"] for e in mux_dag["parents"]},
                {str(mp4.resolve()), str(a_norm.resolve())},
            )

            # Model3d extract-textures
            self.assertGreaterEqual(tex_res["textures"]["count"], 1)
            for tex in tex_res["textures"]["textures"]:
                t_dag = get_asset_lineage(conn, tex["path"])
                self.assertEqual(t_dag["asset"]["operation"], "extract-textures")
                self.assertEqual(t_dag["asset"]["category"], "image")
                self.assertEqual(t_dag["parents"][0]["modality"], "3d")
                self.assertEqual(t_dag["parents"][0]["parent_path"], str(glb.resolve()))
        finally:
            conn.close()


class TestCloudAndUIWorkbenchLineage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill
        for mod in ("image", "audio", "video", "3d"):
            build_skill(mod)

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        self.server, self.gateway_url, self.thread = run_fake_gateway()
        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_PARTNER_TOKEN": "pt_lineage_test",
            "ASYNX_BASE_URL": self.gateway_url,
        }
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)

        self.sample_png = Path(self.temp_dir) / "concept.png"
        PIL.Image.new("RGBA", (64, 64), (255, 80, 0, 255)).save(self.sample_png)
        self.mask_png = Path(self.temp_dir) / "mask.png"
        mask_img = PIL.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        for x in range(16, 48):
            for y in range(16, 48):
                mask_img.putpixel((x, y), (255, 255, 255, 255))
        mask_img.save(self.mask_png)
        self.end_png = Path(self.temp_dir) / "end.png"
        PIL.Image.new("RGBA", (64, 64), (0, 80, 255, 255)).save(self.end_png)

        self.sample_wav = Path(self.temp_dir) / "voice.wav"
        make_test_wav(self.sample_wav)
        self.sample_mp4 = Path(self.temp_dir) / "clip.mp4"
        make_test_mp4(self.sample_mp4)
        self.sample_glb = Path(self.temp_dir) / "model.glb"
        make_test_glb(self.sample_glb)

    def tearDown(self) -> None:
        self.server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_STATE_PATH", None)
        os.environ.pop("ASYNX_CONFIG_PATH", None)

    def run_skill(self, skill_name: str, *args: str) -> dict[str, Any]:
        asynx_bin = REPO_ROOT / "skills" / skill_name / "scripts" / "asynx.py"
        proc = subprocess.run(
            [sys.executable, str(asynx_bin), *with_consent(args)],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"[{skill_name}] stderr={proc.stderr}\nstdout={proc.stdout}")
        return json.loads(proc.stdout)

    def test_cloud_tasks_batches_and_cross_modal_pipeline_lineage(self) -> None:
        # Prepare deterministic fake gateway responses for tasks 1..12
        self.server.asset_data["task_1:0"] = self.sample_png.read_bytes()
        self.server.asset_mimes["task_1:0"] = "image/png"
        self.server.asset_data["task_2:0"] = PIL.Image.new("RGBA", (64, 64), (10, 200, 10, 255)) and self.end_png.read_bytes()
        self.server.asset_mimes["task_2:0"] = "image/png"
        self.server.asset_data["task_3:0"] = self.sample_png.read_bytes()
        self.server.asset_mimes["task_3:0"] = "image/png"
        self.server.asset_data["task_4:0"] = self.sample_png.read_bytes()
        self.server.asset_mimes["task_4:0"] = "image/png"
        self.server.asset_data["task_5:0"] = self.sample_wav.read_bytes()
        self.server.asset_mimes["task_5:0"] = "audio/wav"
        self.server.asset_data["task_6:0"] = self.sample_wav.read_bytes()
        self.server.asset_mimes["task_6:0"] = "audio/wav"
        self.server.asset_data["task_7:0"] = self.sample_wav.read_bytes()
        self.server.asset_mimes["task_7:0"] = "audio/wav"
        self.server.asset_data["task_8:0"] = self.sample_mp4.read_bytes()
        self.server.asset_mimes["task_8:0"] = "video/mp4"
        self.server.asset_data["task_9:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_9:0"] = "model/gltf-binary"
        self.server.asset_data["task_10:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_10:0"] = "model/gltf-binary"
        self.server.asset_data["task_11:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_11:0"] = "model/gltf-binary"
        self.server.asset_data["task_12:0"] = self.sample_glb.read_bytes()
        self.server.asset_mimes["task_12:0"] = "model/gltf-binary"

        out_root = Path(self.temp_dir) / "cloud_out"

        # 1. image.generate with --reference -> task_1
        gen_res = self.run_skill(
            "asynx-image-pro",
            "generate",
            "--prompt",
            "hero portrait",
            "--reference",
            str(self.sample_png),
            "--output-dir",
            str(out_root / "img1"),
        )
        gen_task_id = gen_res["task_id"]
        gen_file = gen_res["files"][0]

        # 2. image.edit --from-task + --mask -> task_2 (multi-parent [gen_file, mask])
        edit_res = self.run_skill(
            "asynx-image-pro",
            "edit",
            "--prompt",
            "golden armor",
            "--from-task",
            gen_task_id,
            "--mask",
            str(self.mask_png),
            "--output-dir",
            str(out_root / "img2"),
        )
        edit_file = edit_res["files"][0]

        # 3. image.generate --detach + wait -> task_3 (lineage preserved across detach/wait)
        det_res = self.run_skill(
            "asynx-image-pro",
            "generate",
            "--prompt",
            "detached variant",
            "--reference",
            str(self.sample_png),
            "--detach",
            "--output-dir",
            str(out_root / "img3"),
        )
        wait_res = self.run_skill(
            "asynx-image-pro",
            "wait",
            det_res["local_id"],
        )
        det_file = wait_res["files"][0]

        # 4. image batch create + poll -> task_4
        b_create = self.run_skill(
            "asynx-image-pro",
            "batch",
            "create",
            "--operation",
            "edit",
            "--prompt",
            "batch armor",
            "--image",
            gen_file,
            "--mask",
            str(self.mask_png),
            "--output-dir",
            str(out_root / "batch"),
        )
        b_id = b_create["batch"]["id"]
        b_wait = self.run_skill("asynx-image-pro", "batch", "poll", b_id)
        batch_file = b_wait["batch"]["files"][0]

        # 5. audio.sfx -> task_5, audio.speech --from-task -> task_6, audio.speech --reference-audio -> task_7
        sfx_res = self.run_skill("asynx-audio", "sfx", "--prompt", "bell", "--output-dir", str(out_root / "aud"))
        sp1_res = self.run_skill(
            "asynx-audio", "speech", "--text", "hello", "--from-task", sfx_res["task_id"], "--output-dir", str(out_root / "aud")
        )
        sp2_res = self.run_skill(
            "asynx-audio", "speech", "--text", "world", "--reference-audio", str(self.sample_wav), "--output-dir", str(out_root / "aud")
        )

        # 6. video.generate --image <edited> --end-image <end.png> -> task_8, then local video spritesheet
        vid_res = self.run_skill(
            "asynx-video",
            "generate",
            "--prompt",
            "hero walks",
            "--image",
            edit_file,
            "--end-image",
            str(self.end_png),
            "--output-dir",
            str(out_root / "vid"),
        )
        vid_file = vid_res["files"][0]
        sheet_res = self.run_skill(
            "asynx-video",
            "video",
            "spritesheet",
            vid_file,
            "--output-dir",
            str(out_root / "sheet"),
            "--fps",
            "5",
        )
        atlas_file = sheet_res["spritesheet"]["atlas_path"]

        # 7. 3d.generate --from-task <gen_task_id> -> task_9 -> rig task_10 -> animate task_11 -> convert task_12
        m3d_res = self.run_skill(
            "asynx-3d", "generate", "--from-task", gen_task_id, "--output-dir", str(out_root / "3d")
        )
        rig_res = self.run_skill(
            "asynx-3d", "rig", "--from-task", m3d_res["task_id"], "--output-dir", str(out_root / "3d")
        )
        anim_res = self.run_skill(
            "asynx-3d", "animate", "--from-task", rig_res["task_id"], "--animation", "walk", "--output-dir", str(out_root / "3d")
        )
        conv_res = self.run_skill(
            "asynx-3d", "convert", "--from-task", anim_res["task_id"], "--output-format", "obj", "--output-dir", str(out_root / "3d")
        )

        conn = connect_db(self.state_db)
        try:
            # Verify image.edit multi-parent & ancestor back to sample_png
            edit_dag = get_asset_lineage(conn, edit_file)
            self.assertEqual(
                {e["parent_path"] for e in edit_dag["parents"]},
                {str(Path(gen_file).resolve()), str(self.mask_png.resolve())},
            )
            self.assertTrue(
                any(e["parent_path"] == str(self.sample_png.resolve()) for e in edit_dag["ancestors"])
            )

            # Verify detached generate + wait lineage
            det_dag = get_asset_lineage(conn, det_file)
            self.assertEqual(det_dag["parents"][0]["parent_path"], str(self.sample_png.resolve()))

            # Verify batch lineage
            batch_dag = get_asset_lineage(conn, batch_file)
            self.assertEqual(
                {e["parent_path"] for e in batch_dag["parents"]},
                {str(Path(gen_file).resolve()), str(self.mask_png.resolve())},
            )

            # Verify audio speech lineages
            sp1_dag = get_asset_lineage(conn, sp1_res["files"][0])
            self.assertEqual(sp1_dag["parents"][0]["parent_path"], str(Path(sfx_res["files"][0]).resolve()))
            sp2_dag = get_asset_lineage(conn, sp2_res["files"][0])
            self.assertEqual(sp2_dag["parents"][0]["parent_path"], str(self.sample_wav.resolve()))

            # Verify cross-modal chain: sample_png -> gen_file -> edit_file (+ end_png) -> vid_file -> atlas_file
            atlas_dag = get_asset_lineage(conn, atlas_file)
            ancestor_paths = {e["parent_path"] for e in atlas_dag["ancestors"]}
            self.assertIn(str(Path(vid_file).resolve()), ancestor_paths)
            self.assertIn(str(Path(edit_file).resolve()), ancestor_paths)
            self.assertIn(str(self.end_png.resolve()), ancestor_paths)
            self.assertIn(str(Path(gen_file).resolve()), ancestor_paths)
            self.assertIn(str(self.sample_png.resolve()), ancestor_paths)

            # Verify 3d chain: gen_file -> m3d -> rig -> anim -> conv
            conv_dag = get_asset_lineage(conn, conv_res["files"][0])
            m3d_ancestors = {e["parent_path"] for e in conv_dag["ancestors"]}
            self.assertIn(str(Path(anim_res["files"][0]).resolve()), m3d_ancestors)
            self.assertIn(str(Path(rig_res["files"][0]).resolve()), m3d_ancestors)
            self.assertIn(str(Path(m3d_res["files"][0]).resolve()), m3d_ancestors)
            self.assertIn(str(Path(gen_file).resolve()), m3d_ancestors)
        finally:
            conn.close()

    def test_ui_workbench_submissions_record_assets_and_lineage(self) -> None:
        client = AsynxClient(self.gateway_url, "pt_ui_test")
        self.server.asset_data["task_1:0"] = self.sample_png.read_bytes()
        self.server.asset_mimes["task_1:0"] = "image/png"

        # 1. image_tools
        tool_out = Path(self.temp_dir) / "ui_tool_out.png"
        s_tools = WorkbenchServer(
            mode="image_tools",
            source_path=str(self.sample_png),
            output_path=str(tool_out),
            output_dir=self.temp_dir,
            static_dir=REPO_ROOT / "src" / "ui" / "image",
            client=client,
        )
        t1 = threading.Thread(target=s_tools.serve_forever, daemon=True)
        t1.start()
        try:
            res = _post_json(
                f"http://127.0.0.1:{s_tools.server_address[1]}/api/submit",
                {"image_data": _data_url_png(), "recipe": "remove_background"},
                s_tools.session_token,
            )
            self.assertTrue(res["ok"])
        finally:
            s_tools.shutdown()
            s_tools.server_close()

        # 2. region_edit
        region_dir = Path(self.temp_dir) / "ui_region"
        s_region = WorkbenchServer(
            mode="region_edit",
            source_path=str(self.sample_png),
            output_path=None,
            output_dir=str(region_dir),
            static_dir=REPO_ROOT / "src" / "ui" / "image",
            client=client,
        )
        t2 = threading.Thread(target=s_region.serve_forever, daemon=True)
        t2.start()
        try:
            mask_png = PIL.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for x in range(16, 48):
                for y in range(16, 48):
                    mask_png.putpixel((x, y), (255, 255, 255, 255))
            mask_buf = io.BytesIO()
            mask_png.save(mask_buf, format="PNG")
            mask_data_url = "data:image/png;base64," + base64.b64encode(mask_buf.getvalue()).decode("ascii")
            res = _post_json(
                f"http://127.0.0.1:{s_region.server_address[1]}/api/submit",
                {
                    "mask": mask_data_url,
                    "annotation_preview": _data_url_png((0, 255, 0, 255)),
                    "annotations": [{"instruction": "add hat"}],
                    "prompt": "wizard hat",
                    "model": "gpt-image-2.5-sunburst",
                },
                s_region.session_token,
            )
            self.assertTrue(res["ok"])
            region_out = res["result"]["files"][0]
        finally:
            s_region.shutdown()
            s_region.server_close()

        # 3. video_frame_sequence
        vid_atlas_dir = Path(self.temp_dir) / "ui_vid_atlas"
        s_vid = WorkbenchServer(
            mode="video_frame_sequence",
            source_path=str(self.sample_mp4),
            output_path=None,
            output_dir=str(vid_atlas_dir),
            static_dir=REPO_ROOT / "src" / "ui" / "video",
            client=client,
        )
        t3 = threading.Thread(target=s_vid.serve_forever, daemon=True)
        t3.start()
        try:
            res = _post_json(
                f"http://127.0.0.1:{s_vid.server_address[1]}/api/submit",
                {
                    "atlas": _data_url_png(size=(128, 128)),
                    "manifest": {"frames": {"f0.png": {"frame": {"x": 0, "y": 0, "w": 64, "h": 64}}}},
                    "metadata": {"fps": 12},
                },
                s_vid.session_token,
            )
            self.assertTrue(res["ok"])
        finally:
            s_vid.shutdown()
            s_vid.server_close()

        # 4. model3d_render_sequence
        m3d_atlas_dir = Path(self.temp_dir) / "ui_3d_atlas"
        s_3d = WorkbenchServer(
            mode="model3d_render_sequence",
            source_path=str(self.sample_glb),
            output_path=None,
            output_dir=str(m3d_atlas_dir),
            static_dir=REPO_ROOT / "src" / "ui" / "3d",
            client=client,
        )
        t4 = threading.Thread(target=s_3d.serve_forever, daemon=True)
        t4.start()
        try:
            res = _post_json(
                f"http://127.0.0.1:{s_3d.server_address[1]}/api/submit",
                {
                    "atlas": _data_url_png(size=(128, 128)),
                    "manifest": {"frames": {"d0.png": {"frame": {"x": 0, "y": 0, "w": 64, "h": 64}}}},
                },
                s_3d.session_token,
            )
            self.assertTrue(res["ok"])
        finally:
            s_3d.shutdown()
            s_3d.server_close()

        conn = connect_db(self.state_db)
        try:
            tool_dag = get_asset_lineage(conn, tool_out)
            self.assertEqual(tool_dag["asset"]["source_kind"], "ui_export")
            self.assertEqual(tool_dag["asset"]["operation"], "image_tools")
            self.assertEqual(tool_dag["parents"][0]["parent_path"], str(self.sample_png.resolve()))

            region_dag = get_asset_lineage(conn, region_out)
            self.assertEqual(region_dag["asset"]["source_kind"], "cloud_task")
            self.assertEqual(region_dag["parents"][0]["parent_path"], str(self.sample_png.resolve()))

            vid_dag = get_asset_lineage(conn, vid_atlas_dir / "atlas.png")
            self.assertEqual(vid_dag["asset"]["source_kind"], "ui_export")
            self.assertEqual(vid_dag["asset"]["category"], "frame_sequence")
            self.assertEqual(vid_dag["asset"]["operation"], "frame_sequence")
            self.assertEqual(vid_dag["parents"][0]["parent_path"], str(self.sample_mp4.resolve()))

            m3d_dag = get_asset_lineage(conn, m3d_atlas_dir / "atlas.png")
            self.assertEqual(m3d_dag["asset"]["source_kind"], "ui_export")
            self.assertEqual(m3d_dag["asset"]["category"], "frame_sequence")
            self.assertEqual(m3d_dag["asset"]["operation"], "render_sequence")
            self.assertEqual(m3d_dag["parents"][0]["parent_path"], str(self.sample_glb.resolve()))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
