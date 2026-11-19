from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

import PIL.Image

from shared.asxlib.state import (
    compute_file_sha256,
    connect_db,
    create_task_intent,
    get_asset_by_id,
    get_asset_lineage,
    get_task,
    record_artifact_with_lineage,
)


from tests.test_issue_03_audio_skill import make_test_wav
from tests.test_issue_04_video_skill import make_test_mp4
from tests.test_issue_05_3d_skill import make_test_glb


class TestStateRelinkingHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="asx-t04-state-"))
        self.db_path = self.temp_dir / "state.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_all_assets_and_relink_moved_asset(self) -> None:
        from shared.asxlib.state import list_all_assets, relink_moved_asset

        old_dir = self.temp_dir / "raw"
        new_dir = self.temp_dir / "organized" / "sprites"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)

        parent_file = old_dir / "concept.png"
        PIL.Image.new("RGBA", (32, 32), (200, 40, 40, 255)).save(parent_file)

        child_file = old_dir / "hero.png"
        PIL.Image.new("RGBA", (64, 64), (40, 200, 40, 255)).save(child_file)

        grandchild_file = old_dir / "hero_small.png"
        PIL.Image.new("RGBA", (16, 16), (40, 40, 200, 255)).save(grandchild_file)

        conn = connect_db(self.db_path)
        try:
            p_asset = record_artifact_with_lineage(
                conn,
                file_path=parent_file,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                metadata={"prompt": "hero concept"},
            )
            c_asset = record_artifact_with_lineage(
                conn,
                file_path=child_file,
                modality="image",
                operation="edit",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                parent_paths=[parent_file],
                metadata={"prompt": "add cape"},
            )
            g_asset = record_artifact_with_lineage(
                conn,
                file_path=grandchild_file,
                modality="image",
                operation="resize",
                source_kind="local_process",
                parent_paths=[child_file],
            )

            all_assets = list_all_assets(conn)
            self.assertEqual(len(all_assets), 3)
            self.assertTrue(all("task_operation" in item and "model" in item for item in all_assets))

            # Move middle node (child_file) on disk to new_dir / "hero_renamed.png"
            moved_child = new_dir / "hero_renamed.png"
            shutil.move(str(child_file), str(moved_child))
            child_sha = compute_file_sha256(moved_child)

            updated = relink_moved_asset(
                conn,
                c_asset["artifact_id"],
                moved_child,
                sha256=child_sha,
            )
            self.assertEqual(updated["path"], str(moved_child.resolve()))

            # Check tasks.output_dir updated cleanly
            task_row = get_task(conn, c_asset["local_task_id"])
            self.assertEqual(task_row["output_dir"], str(new_dir.resolve()))

            # Check lineage paths updated on both parent->child and child->grandchild edges
            dag = get_asset_lineage(conn, c_asset["artifact_id"])
            self.assertEqual(dag["path"], str(moved_child.resolve()))
            self.assertEqual(dag["parents"][0]["child_path"], str(moved_child.resolve()))
            self.assertEqual(dag["parents"][0]["parent_artifact_id"], p_asset["artifact_id"])
            self.assertEqual(dag["children"][0]["parent_path"], str(moved_child.resolve()))
            self.assertEqual(dag["children"][0]["child_artifact_id"], g_asset["artifact_id"])

            # Lookup by new path works
            by_new_path = get_asset_lineage(conn, moved_child)
            self.assertEqual(by_new_path["asset"]["artifact_id"], c_asset["artifact_id"])
            self.assertEqual(len(by_new_path["parents"]), 1)
            self.assertEqual(len(by_new_path["children"]), 1)
        finally:
            conn.close()


class TestWorkspaceScanner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="asx-t04-scan-"))
        self.workspace = self.temp_dir / "project"
        self.workspace.mkdir(parents=True)
        self.db_path = self.temp_dir / "state.db"
        self.cache_dir = self.temp_dir / "cache"
        self.cache_dir.mkdir(parents=True)
        os.environ["ASYNX_STATE_PATH"] = str(self.db_path)
        os.environ["ASYNX_CACHE_PATH"] = str(self.cache_dir / "models.json")

    def tearDown(self) -> None:
        os.environ.pop("ASYNX_STATE_PATH", None)
        os.environ.pop("ASYNX_CACHE_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_five_category_scan_exclusions_frame_sequences_and_ledger_join(self) -> None:
        from src.asset_manager.scanner import scan_workspace

        # Excluded engineering directories (must all be skipped)
        for excluded in (
            ".git",
            "node_modules",
            ".venv",
            "__pycache__",
            "dist",
            ".scratch",
            ".pytest_cache",
            ".pi",
            "venv",
            ".mypy_cache",
        ):
            d = self.workspace / excluded
            d.mkdir(parents=True)
            PIL.Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(d / "ignored.png")

        # 1. Managed cloud image -> local edited image
        img_dir = self.workspace / "assets" / "images"
        img_dir.mkdir(parents=True)
        cloud_img = img_dir / "hero_concept.png"
        PIL.Image.new("RGBA", (64, 64), (255, 100, 0, 255)).save(cloud_img)

        edited_img = img_dir / "hero_resized.png"
        PIL.Image.new("RGBA", (32, 32), (255, 120, 0, 255)).save(edited_img)

        # External image
        ext_img = img_dir / "ui_border.webp"
        PIL.Image.new("RGBA", (32, 32), (0, 120, 255, 255)).save(ext_img)

        # 2. Audio (1 managed + 1 external)
        aud_dir = self.workspace / "assets" / "audio"
        aud_dir.mkdir(parents=True)
        managed_wav = aud_dir / "sword_slash.wav"
        make_test_wav(managed_wav)
        ext_mp3 = aud_dir / "bgm.mp3"
        ext_mp3.write_bytes(b"ID3" + b"\x00" * 64)

        # 3. Video (1 managed)
        vid_dir = self.workspace / "assets" / "video"
        vid_dir.mkdir(parents=True)
        managed_mp4 = vid_dir / "walk_cycle.mp4"
        make_test_mp4(managed_mp4)

        # 4. Frame sequences (must NOT duplicate atlas images under image!)
        # 4a: standard atlas.png + manifest.json
        fs_dir_a = self.workspace / "assets" / "sprites" / "hero_walk"
        fs_dir_a.mkdir(parents=True)
        atlas_a = fs_dir_a / "atlas.png"
        PIL.Image.new("RGBA", (128, 64), (0, 255, 128, 255)).save(atlas_a)
        manifest_a = fs_dir_a / "manifest.json"
        manifest_a.write_text(
            json.dumps(
                {
                    "frames": {
                        "frame-0000.png": {"frame": {"x": 0, "y": 0, "w": 64, "h": 64}},
                        "frame-0001.png": {"frame": {"x": 64, "y": 0, "w": 64, "h": 64}},
                    },
                    "meta": {"image": "atlas.png", "fps": 10},
                }
            ),
            encoding="utf-8",
        )

        # 4b: <stem>.manifest.json + custom atlas name via meta.image (external)
        fs_dir_b = self.workspace / "assets" / "sprites" / "coin"
        fs_dir_b.mkdir(parents=True)
        atlas_b = fs_dir_b / "coin_sheet.webp"
        PIL.Image.new("RGBA", (64, 64), (255, 220, 0, 255)).save(atlas_b)
        manifest_b = fs_dir_b / "coin_spin.manifest.json"
        manifest_b.write_text(
            json.dumps(
                {
                    "frames": [{"x": 0, "y": 0, "w": 32, "h": 32}],
                    "meta": {"image": "coin_sheet.webp"},
                }
            ),
            encoding="utf-8",
        )

        # 5. 3D model (1 managed)
        m3d_dir = self.workspace / "assets" / "models"
        m3d_dir.mkdir(parents=True)
        managed_glb = m3d_dir / "knight.glb"
        make_test_glb(managed_glb)

        # Non-asset JSON/text file should be ignored
        (self.workspace / "README.md").write_text("# game\n", encoding="utf-8")

        # Seed ledger with cloud & local tasks + lineage
        conn = connect_db(self.db_path)
        try:
            t_cloud = create_task_intent(
                conn,
                operation="generate",
                model="gpt-image-2.5-sunburst",
                idempotency_key="idem-cloud-1",
                request={"input": {"prompt": "fantasy knight portrait"}},
                output_dir=str(img_dir),
                status="succeeded",
                modality="image",
            )
            from shared.asxlib.state import bind_remote_task
            bind_remote_task(conn, t_cloud["local_id"], "task_remote_img_1", status="succeeded")
            cloud_art = record_artifact_with_lineage(
                conn,
                file_path=cloud_img,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                local_task_id=t_cloud["local_id"],
                task_id="task_remote_img_1",
                model="gpt-image-2.5-sunburst",
            )
            record_artifact_with_lineage(
                conn,
                file_path=edited_img,
                modality="image",
                operation="resize",
                source_kind="local_process",
                parent_paths=[cloud_img],
                model="local",
            )
            record_artifact_with_lineage(
                conn,
                file_path=managed_wav,
                modality="audio",
                operation="sfx",
                source_kind="cloud_task",
                model="audio-sfx-1",
                metadata={"prompt": "steel sword slash"},
            )
            record_artifact_with_lineage(
                conn,
                file_path=managed_mp4,
                modality="video",
                operation="generate",
                source_kind="cloud_task",
                parent_paths=[cloud_img],
                model="video-gen-1",
            )
            record_artifact_with_lineage(
                conn,
                file_path=atlas_a,
                modality="video",
                operation="spritesheet",
                source_kind="local_process",
                category="frame_sequence",
                parent_paths=[managed_mp4],
                metadata={"manifest_path": str(manifest_a.resolve()), "fps": 10},
            )
            record_artifact_with_lineage(
                conn,
                file_path=managed_glb,
                modality="3d",
                operation="generate",
                source_kind="cloud_task",
                parent_paths=[cloud_img],
                model="3d-mesh-1",
            )
        finally:
            conn.close()

        report = scan_workspace(self.workspace)
        self.assertTrue(report["ok"])
        self.assertEqual(report["schema_version"], 3)

        # Total: 3 images (cloud_img, edited_img, ext_img), 2 audio, 2 frame_sequence, 1 video, 1 3d = 9
        self.assertEqual(report["counts"]["total"], 9)
        self.assertEqual(report["counts"]["managed"], 6)
        self.assertEqual(report["counts"]["external"], 3)
        self.assertEqual(
            report["counts"]["by_category"],
            {"image": 3, "audio": 2, "frame_sequence": 2, "video": 1, "3d": 1},
        )

        by_path = {item["path"]: item for item in report["assets"]}
        self.assertNotIn(str((self.workspace / ".git" / "ignored.png").resolve()), by_path)

        # Claimed atlases must appear as frame_sequence, never duplicated as image
        atlas_a_item = by_path[str(atlas_a.resolve())]
        self.assertEqual(atlas_a_item["category"], "frame_sequence")
        self.assertTrue(atlas_a_item["managed"])
        self.assertEqual(atlas_a_item["source_kind"], "local_process")
        self.assertEqual(atlas_a_item["operation"], "spritesheet")
        self.assertEqual(atlas_a_item["metadata"]["frame_count"], 2)
        self.assertEqual(len(atlas_a_item["parents"]), 1)

        atlas_b_item = by_path[str(atlas_b.resolve())]
        self.assertEqual(atlas_b_item["category"], "frame_sequence")
        self.assertFalse(atlas_b_item["managed"])
        self.assertEqual(atlas_b_item["source_kind"], "external")

        # Cloud image metadata & children
        cloud_item = by_path[str(cloud_img.resolve())]
        self.assertTrue(cloud_item["managed"])
        self.assertEqual(cloud_item["source_kind"], "cloud_task")
        self.assertEqual(cloud_item["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(cloud_item["prompt"], "fantasy knight portrait")
        self.assertEqual(cloud_item["task_id"], "task_remote_img_1")
        self.assertEqual(cloud_item["artifact_id"], cloud_art["artifact_id"])
        self.assertEqual(len(cloud_item["children"]), 3)  # edited_img, managed_mp4, managed_glb

        # Filters
        fs_only = scan_workspace(self.workspace, category="frame_sequence")
        self.assertEqual(len(fs_only["assets"]), 2)

        managed_imgs = scan_workspace(self.workspace, category="image", managed_only=True)
        self.assertEqual(len(managed_imgs["assets"]), 2)

        ext_only = scan_workspace(self.workspace, external_only=True)
        self.assertEqual(len(ext_only["assets"]), 3)

        queried = scan_workspace(self.workspace, query="fantasy knight")
        self.assertEqual([a["path"] for a in queried["assets"]], [str(cloud_img.resolve())])

    def test_sha256_cache_hits_on_unchanged_and_invalidates_on_change(self) -> None:
        from unittest.mock import patch
        from src.asset_manager.scanner import scan_workspace

        img = self.workspace / "cached.png"
        PIL.Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(img)

        first = scan_workspace(self.workspace)
        sha1 = first["assets"][0]["sha256"]

        with patch("src.asset_manager.scanner.compute_file_sha256", side_effect=AssertionError("should use cache")):
            second = scan_workspace(self.workspace)
        self.assertEqual(second["assets"][0]["sha256"], sha1)

        # Modify file (size/mtime changes) -> must recompute
        PIL.Image.new("RGBA", (48, 48), (90, 80, 70, 255)).save(img)
        third = scan_workspace(self.workspace)
        self.assertNotEqual(third["assets"][0]["sha256"], sha1)

    def test_move_rename_resilience_preserves_badge_lineage_and_ignores_copies(self) -> None:
        from src.asset_manager.scanner import asset_lineage_report, scan_workspace

        raw_dir = self.workspace / "raw"
        raw_dir.mkdir(parents=True)
        concept = raw_dir / "concept.png"
        PIL.Image.new("RGBA", (64, 64), (210, 50, 50, 255)).save(concept)

        hero = raw_dir / "hero.png"
        PIL.Image.new("RGBA", (64, 64), (50, 210, 50, 255)).save(hero)

        fs_old_dir = raw_dir / "walk_seq"
        fs_old_dir.mkdir()
        atlas_old = fs_old_dir / "atlas.png"
        PIL.Image.new("RGBA", (128, 64), (50, 50, 210, 255)).save(atlas_old)
        manifest_old = fs_old_dir / "manifest.json"
        manifest_old.write_text(
            json.dumps(
                {
                    "frames": {"f0.png": {"frame": {"x": 0, "y": 0, "w": 64, "h": 64}}},
                    "meta": {"image": "atlas.png", "fps": 12},
                }
            ),
            encoding="utf-8",
        )

        conn = connect_db(self.db_path)
        try:
            c_art = record_artifact_with_lineage(
                conn,
                file_path=concept,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                metadata={"prompt": "concept art"},
            )
            h_art = record_artifact_with_lineage(
                conn,
                file_path=hero,
                modality="image",
                operation="edit",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                parent_paths=[concept],
                metadata={"prompt": "armored hero"},
            )
            fs_art = record_artifact_with_lineage(
                conn,
                file_path=atlas_old,
                modality="video",
                operation="spritesheet",
                source_kind="local_process",
                category="frame_sequence",
                parent_paths=[hero],
                metadata={"manifest_path": str(manifest_old.resolve()), "fps": 12},
            )
        finally:
            conn.close()

        # Copy concept.png while keeping original intact -> copy must remain external
        backup_dir = self.workspace / "backups"
        backup_dir.mkdir()
        concept_copy = backup_dir / "concept_copy.png"
        shutil.copy2(concept, concept_copy)

        # Move & rename hero.png -> characters/knight_final.png
        char_dir = self.workspace / "characters"
        char_dir.mkdir()
        hero_moved = char_dir / "knight_final.png"
        shutil.move(str(hero), str(hero_moved))

        # Move & rename frame_sequence bundle -> sprites/knight_walk/walk_atlas.png + walk.manifest.json
        fs_new_dir = self.workspace / "sprites" / "knight_walk"
        fs_new_dir.mkdir(parents=True)
        atlas_moved = fs_new_dir / "walk_atlas.png"
        manifest_moved = fs_new_dir / "walk.manifest.json"
        shutil.move(str(atlas_old), str(atlas_moved))
        manifest_old.unlink()
        manifest_moved.write_text(
            json.dumps(
                {
                    "frames": {"f0.png": {"frame": {"x": 0, "y": 0, "w": 64, "h": 64}}},
                    "meta": {"image": "walk_atlas.png", "fps": 12},
                }
            ),
            encoding="utf-8",
        )

        report = scan_workspace(self.workspace)
        self.assertEqual(report["counts"]["relinked"], 2)
        self.assertEqual(report["counts"]["managed"], 3)
        self.assertEqual(report["counts"]["external"], 1)

        by_path = {a["path"]: a for a in report["assets"]}

        # Original concept still managed, copy is external
        self.assertTrue(by_path[str(concept.resolve())]["managed"])
        self.assertFalse(by_path[str(concept_copy.resolve())]["managed"])
        self.assertEqual(by_path[str(concept_copy.resolve())]["source_kind"], "external")

        # Moved hero retains artifact_id, managed badge, relinked_from, parent, and child
        h_item = by_path[str(hero_moved.resolve())]
        self.assertTrue(h_item["managed"])
        self.assertEqual(h_item["artifact_id"], h_art["artifact_id"])
        self.assertEqual(h_item["relinked_from"], str(hero.resolve()))
        self.assertEqual(h_item["parents"][0]["parent_path"], str(concept.resolve()))
        self.assertEqual(h_item["children"][0]["child_path"], str(atlas_moved.resolve()))

        # Moved frame_sequence retains artifact_id, category, updated manifest_path, and ancestor chain
        fs_item = by_path[str(atlas_moved.resolve())]
        self.assertEqual(fs_item["category"], "frame_sequence")
        self.assertTrue(fs_item["managed"])
        self.assertEqual(fs_item["artifact_id"], fs_art["artifact_id"])
        self.assertEqual(fs_item["metadata"]["manifest_path"], str(manifest_moved.resolve()))

        # Lineage report by new path traces full upstream back to concept.png
        lin = asset_lineage_report(atlas_moved)
        self.assertTrue(lin["ok"])
        self.assertEqual(lin["asset"]["artifact_id"], fs_art["artifact_id"])
        self.assertEqual(lin["parents"][0]["parent_path"], str(hero_moved.resolve()))
        ancestor_paths = {e["parent_path"] for e in lin["ancestors"]}
        self.assertEqual(ancestor_paths, {str(hero_moved.resolve()), str(concept.resolve())})


class TestAssetManagerCLIEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill
        build_skill("asset_manager")

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="asx-t04-cli-"))
        self.workspace = self.temp_dir / "game_project"
        self.workspace.mkdir(parents=True)
        self.state_db = self.temp_dir / "state.db"
        self.config_file = self.temp_dir / "config.json"
        self.cache_file = self.temp_dir / "cache" / "models.json"
        self.asynx_bin = REPO_ROOT / "skills" / "asynx-asset-manager" / "scripts" / "asynx.py"
        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_CACHE_PATH": str(self.cache_file),
        }
        self.env.pop("ASYNX_PARTNER_TOKEN", None)
        self.env.pop("ASYNX_BASE_URL", None)
        self.env["PLAIERS_SKILL_CONTEXT"] = str(self.temp_dir / "no-context" / ".plaiers-context.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_cli(self, *args: str, expected_code: int = 0) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, str(self.asynx_bin), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(
            proc.returncode,
            expected_code,
            f"args={args}\nstderr={proc.stderr}\nstdout={proc.stdout}",
        )
        return json.loads(proc.stdout)

    def test_cli_scan_list_lineage_and_move_relink(self) -> None:
        raw_dir = self.workspace / "raw"
        raw_dir.mkdir(parents=True)
        src_img = raw_dir / "sword.png"
        PIL.Image.new("RGBA", (64, 64), (180, 180, 220, 255)).save(src_img)
        ext_wav = self.workspace / "ambient.wav"
        make_test_wav(ext_wav)

        child_dir = self.workspace / "generated"
        child_dir.mkdir()
        edited_img = child_dir / "sword_glow.png"
        PIL.Image.new("RGBA", (64, 64), (255, 220, 100, 255)).save(edited_img)

        conn = connect_db(self.state_db)
        try:
            parent_art = record_artifact_with_lineage(
                conn,
                file_path=src_img,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                metadata={"prompt": "steel sword"},
            )
            child_art = record_artifact_with_lineage(
                conn,
                file_path=edited_img,
                modality="image",
                operation="edit",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                parent_paths=[src_img],
                metadata={"prompt": "glowing runes"},
            )
        finally:
            conn.close()

        # 1. scan --root
        scan_out = self._run_cli("scan", "--root", str(self.workspace))
        self.assertTrue(scan_out["ok"])
        self.assertEqual(scan_out["counts"]["total"], 3)
        self.assertEqual(scan_out["counts"]["managed"], 2)
        self.assertEqual(scan_out["counts"]["external"], 1)

        # 2. list --category image --managed-only
        list_out = self._run_cli(
            "list",
            "--root",
            str(self.workspace),
            "--category",
            "image",
            "--managed-only",
            "--query",
            "glowing",
        )
        self.assertTrue(list_out["ok"])
        self.assertEqual(len(list_out["assets"]), 1)
        self.assertEqual(list_out["assets"][0]["artifact_id"], child_art["artifact_id"])

        # 3. Move edited_img on disk -> scan -> lineage by new path and artifact_id
        moved_dir = self.workspace / "weapons"
        moved_dir.mkdir()
        moved_sword = moved_dir / "runeblade.png"
        shutil.move(str(edited_img), str(moved_sword))

        rescan = self._run_cli("scan", "--root", str(self.workspace))
        self.assertEqual(rescan["counts"]["relinked"], 1)

        lin_by_path = self._run_cli("lineage", str(moved_sword))
        self.assertTrue(lin_by_path["ok"])
        self.assertEqual(lin_by_path["asset"]["artifact_id"], child_art["artifact_id"])
        self.assertEqual(lin_by_path["parents"][0]["parent_artifact_id"], parent_art["artifact_id"])

        lin_by_id = self._run_cli("lineage", parent_art["artifact_id"])
        self.assertTrue(lin_by_id["ok"])
        self.assertEqual(lin_by_id["children"][0]["child_path"], str(moved_sword.resolve()))

        # 4. Shared utility/auth commands: doctor, config status, whoami
        cfg = self._run_cli("config", "status")
        self.assertTrue(cfg["ok"])

        doc = self._run_cli("doctor", expected_code=2)
        self.assertIn("checks", doc)

        need_login = self._run_cli("whoami", expected_code=2)
        self.assertFalse(need_login["ok"])
        self.assertEqual(need_login["error"]["code"], "need_login")

        missing_lin = self._run_cli("lineage", "artifact_does_not_exist", expected_code=1)
        self.assertFalse(missing_lin["ok"])
        self.assertEqual(missing_lin["error"]["code"], "asset_not_found")

        # 5. whoami / doctor --verify via the credential-context bridge + FakeGatewayServer
        from tests.fake_gateway import run_fake_gateway
        from tests.fake_sagein_server import install_fake_context, run_fake_sagein_server

        gw_server, gw_url, _ = run_fake_gateway()
        self.addCleanup(gw_server.shutdown)
        sagein, origin, _ = run_fake_sagein_server(gw_url)
        self.addCleanup(sagein.shutdown)
        self.config_file.write_text("{}", encoding="utf-8")
        self.env.pop("ASYNX_PARTNER_TOKEN", None)
        self.env.pop("ASYNX_BASE_URL", None)
        context, _ = install_fake_context(Path(self.temp_dir) / "skill", origin)
        self.env["PLAIERS_SKILL_CONTEXT"] = str(context)

        who = self._run_cli("whoami")
        self.assertTrue(who["ok"])
        self.assertEqual(who["origin"], origin)

        doc_ver = self._run_cli("doctor", "--verify")
        self.assertTrue(doc_ver["ok"])
        self.assertTrue(doc_ver["verification"]["ok"])

    def test_asset_manager_ui_placeholder_serves_session_and_index(self) -> None:
        import threading
        import urllib.request
        from shared.asxlib.ui_server import WorkbenchServer

        ui_static = REPO_ROOT / "skills" / "asynx-asset-manager" / "scripts" / "asxlib" / "ui"
        srv = WorkbenchServer(
            mode="asset_manager",
            source_path=None,
            output_path=None,
            output_dir=str(self.workspace),
            static_dir=ui_static,
            metadata={"workspace_root": str(self.workspace.resolve())},
            client=None,
        )
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            port = srv.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                html = resp.read().decode("utf-8")
                self.assertIn("Asynx Asset Manager Workbench", html)
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/session",
                headers={"X-Asx-Session-Token": srv.session_token},
            )
            with urllib.request.urlopen(req) as resp:
                sess = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(sess["ok"])
                self.assertEqual(sess["mode"], "asset_manager")
                self.assertEqual(sess["metadata"]["workspace_root"], str(self.workspace.resolve()))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
