from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
from tests.interpreter_pillow import add_interpreter_pillow  # noqa: E402
from tests.charge_args import with_ui_consent  # noqa: E402

add_interpreter_pillow()

import PIL.Image

from shared.asxlib.state import connect_db, get_asset_lineage, record_artifact_with_lineage
from shared.asxlib.ui_server import WorkbenchServer
from tests.fake_gateway import run_fake_gateway
from tests.fake_sagein_server import install_fake_context, run_fake_sagein_server
from tests.test_issue_03_audio_skill import make_test_wav
from tests.test_issue_04_video_skill import make_test_mp4
from tests.test_issue_05_3d_skill import make_test_glb


def _data_url_png(color: tuple[int, int, int, int] = (255, 80, 40, 255), size: tuple[int, int] = (64, 64)) -> str:
    img = PIL.Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers: dict[str, str] = {}
    if token:
        headers["X-Asx-Session-Token"] = token
    data_bytes = None
    body = with_ui_consent(url, body) if method == "POST" else body
    if body is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _http_bytes(
    url: str,
    *,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    headers = dict(extra_headers or {})
    if token:
        headers["X-Asx-Session-Token"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


class TestAssetManagerPackagingDeterminism(unittest.TestCase):
    def test_asset_manager_zip_contains_cross_modal_modules_embedded_ui_and_no_pycache(self) -> None:
        from src.build_skills import package_skill

        zip_path, manifest_1 = package_skill("asset_manager")
        install_receipt_1 = (REPO_ROOT / "skills" / "plaiers-asynx-workbench" / ".asx-install.json").read_text(encoding="utf-8")
        sha_1 = json.loads(manifest_1.read_text(encoding="utf-8"))["sha256"]
        package_skill("asset_manager")
        install_receipt_2 = (REPO_ROOT / "skills" / "plaiers-asynx-workbench" / ".asx-install.json").read_text(encoding="utf-8")
        sha_2 = json.loads(manifest_1.read_text(encoding="utf-8"))["sha256"]
        self.assertEqual(install_receipt_1, install_receipt_2)
        self.assertEqual(sha_1, sha_2)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())

        self.assertFalse(any("__pycache__" in n or n.endswith((".pyc", ".pyo")) for n in names))

        expected_modules = (
            "plaiers-asynx-workbench/scripts/asxlib/cli.py",
            "plaiers-asynx-workbench/scripts/asxlib/scanner.py",
            "plaiers-asynx-workbench/scripts/asxlib/actions.py",
            "plaiers-asynx-workbench/scripts/asxlib/images.py",
            "plaiers-asynx-workbench/scripts/asxlib/local_images.py",
            "plaiers-asynx-workbench/scripts/asxlib/image_results.py",
            "plaiers-asynx-workbench/scripts/asxlib/reference_media.py",
            "plaiers-asynx-workbench/scripts/asxlib/audio_tasks.py",
            "plaiers-asynx-workbench/scripts/asxlib/local_audio.py",
            "plaiers-asynx-workbench/scripts/asxlib/video_tasks.py",
            "plaiers-asynx-workbench/scripts/asxlib/local_video.py",
            "plaiers-asynx-workbench/scripts/asxlib/model3d_tasks.py",
            "plaiers-asynx-workbench/scripts/asxlib/local_model3d.py",
            "plaiers-asynx-workbench/scripts/asxlib/ui/index.html",
            "plaiers-asynx-workbench/scripts/asxlib/ui/account-bar.js",
            "plaiers-asynx-workbench/scripts/asxlib/ui/model3d-loader.js",
            "plaiers-asynx-workbench/scripts/asxlib/ui/image/index.html",
            "plaiers-asynx-workbench/scripts/asxlib/ui/video/index.html",
            "plaiers-asynx-workbench/scripts/asxlib/ui/3d/index.html",
        )
        for item in expected_modules:
            self.assertIn(item, names, f"Missing bundled asset in zip: {item}")

        # Verify cli.py inside plaiers-asynx-workbench is asset_manager/cli.py, not overwritten by another modality
        with zipfile.ZipFile(zip_path, "r") as zf:
            cli_src = zf.read("plaiers-asynx-workbench/scripts/asxlib/cli.py").decode("utf-8")
        self.assertIn('"scan"', cli_src)
        self.assertIn('"lineage"', cli_src)


class TestAssetManagerWorkbenchBackend(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="asx-t05-wb-"))
        self.workspace = self.temp_dir / "project"
        self.workspace.mkdir(parents=True)
        self.outside_file = self.temp_dir / "secret_outside.txt"
        self.outside_file.write_text("do-not-leak", encoding="utf-8")

        self.state_db = self.temp_dir / "state.db"
        self.config_file = self.temp_dir / "config.json"
        self.cache_file = self.temp_dir / "cache" / "models.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        self._prev_env = {
            k: os.environ.get(k)
            for k in ("ASYNX_STATE_PATH", "ASYNX_CONFIG_PATH", "ASYNX_CACHE_PATH", "ASYNX_PARTNER_TOKEN", "ASYNX_BASE_URL", "PLAIERS_SKILL_CONTEXT")
        }
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)
        os.environ["ASYNX_CACHE_PATH"] = str(self.cache_file)
        os.environ.pop("ASYNX_PARTNER_TOKEN", None)
        os.environ.pop("ASYNX_BASE_URL", None)

        self.gw_server, self.gw_url, _ = run_fake_gateway()
        self.login_server, self.login_url, _ = run_fake_sagein_server(self.gw_url)
        # No credential context yet: the workbench starts unauthenticated.
        self.skill_dir = self.temp_dir / "skill"
        os.environ["PLAIERS_SKILL_CONTEXT"] = str(self.skill_dir / ".plaiers-context.json")

        # Seed workspace assets across all 5 categories
        (self.workspace / "art").mkdir(parents=True)
        self.hero_img = self.workspace / "art" / "hero.png"
        # Create a 64x64 image with solid white border and colorful quadrants for slice/rembg testing
        img = PIL.Image.new("RGBA", (64, 64), (255, 255, 255, 255))
        for y in range(10, 54):
            for x in range(10, 54):
                img.putpixel((x, y), (40, 120, 240, 255))
        img.save(self.hero_img)

        self.sample_wav = self.workspace / "audio" / "voice.wav"
        self.sample_wav.parent.mkdir(parents=True)
        make_test_wav(self.sample_wav)

        self.sample_mp4 = self.workspace / "video" / "clip.mp4"
        self.sample_mp4.parent.mkdir(parents=True)
        make_test_mp4(self.sample_mp4)

        self.sample_glb = self.workspace / "models" / "hero.glb"
        self.sample_glb.parent.mkdir(parents=True)
        make_test_glb(self.sample_glb)

        # Seed fake gateway assets for task_1..task_30
        png_bytes = self.hero_img.read_bytes()
        mp4_bytes = self.sample_mp4.read_bytes()
        glb_bytes = self.sample_glb.read_bytes()
        wav_bytes = self.sample_wav.read_bytes()
        for idx in range(1, 30):
            # Dynamically populated per test or default to png/mp4/glb/wav based on task_type via wrapper if needed
            self.gw_server.asset_data[f"task_{idx}:0"] = png_bytes
            self.gw_server.asset_mimes[f"task_{idx}:0"] = "image/png"

        # Record initial managed asset so workspace has both managed and external
        conn = connect_db(self.state_db)
        try:
            self.hero_art = record_artifact_with_lineage(
                conn,
                file_path=self.hero_img,
                modality="image",
                operation="generate",
                source_kind="cloud_task",
                model="gpt-image-2.5-sunburst",
                metadata={"prompt": "hero concept"},
            )
        finally:
            conn.close()

        self.server = WorkbenchServer(
            mode="asset_manager",
            source_path=None,
            output_path=None,
            output_dir=str(self.workspace / "generated-assets"),
            static_dir=REPO_ROOT / "src" / "ui" / "asset_manager",
            metadata={"workspace_root": str(self.workspace.resolve())},
            client=None,
        )
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token = self.server.session_token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.gw_server.shutdown()
        self.login_server.shutdown()
        for k, v in self._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _login_via_workbench(self) -> None:
        """Install the credential context (as the Plaiers installer would); the JWT is then obtained lazily."""
        install_fake_context(self.skill_dir, self.login_url)
        status, payload = _http_json(f"{self.base_url}/api/auth/status", token=self.token)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["authenticated"])

    def _set_next_gateway_assets(self, mime: str, data: bytes) -> None:
        # Next task_id on FakeGateway is task_{len(tasks) + 1}
        next_idx = len(self.gw_server.tasks) + 1
        self.gw_server.asset_data[f"task_{next_idx}:0"] = data
        self.gw_server.asset_mimes[f"task_{next_idx}:0"] = mime

    def test_assets_lineage_workspace_file_confinement_and_lazy_auth(self) -> None:
        # 1. Auth token required
        status, _ = _http_json(f"{self.base_url}/api/assets")
        self.assertEqual(status, 401)

        # 2. Static UI & embedded sub-workbench routes served cleanly
        for rel in ("/", "/account-bar.js", "/image/index.html", "/video/index.html", "/3d/index.html"):
            st, body, _hdrs = _http_bytes(f"{self.base_url}{rel}")
            self.assertEqual(st, 200, f"Failed serving {rel}")
            self.assertGreater(len(body), 100)

        # 3. GET /api/assets with filters
        st, all_res = _http_json(f"{self.base_url}/api/assets", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(all_res["counts"]["total"], 4)
        self.assertEqual(all_res["counts"]["managed"], 1)
        self.assertEqual(all_res["counts"]["external"], 3)

        st, img_res = _http_json(f"{self.base_url}/api/assets?category=image&managed_only=1", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(len(img_res["assets"]), 1)
        self.assertEqual(img_res["assets"][0]["path"], str(self.hero_img.resolve()))

        # 4. GET /api/lineage by query & path segment
        q_target = urllib.parse.quote(self.hero_art["artifact_id"])
        st, lin1 = _http_json(f"{self.base_url}/api/lineage?target={q_target}", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(lin1["asset"]["artifact_id"], self.hero_art["artifact_id"])

        st, lin2 = _http_json(f"{self.base_url}/api/lineage/{q_target}", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(lin2["path"], str(self.hero_img.resolve()))

        # 5. GET /api/workspace-file serves confined files (including Range) and rejects traversal with 403
        q_hero = urllib.parse.quote(str(self.hero_img))
        st, data, _ = _http_bytes(f"{self.base_url}/api/workspace-file?path={q_hero}", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(data, self.hero_img.read_bytes())

        st, partial, hdrs = _http_bytes(
            f"{self.base_url}/api/workspace-file?path={q_hero}",
            token=self.token,
            extra_headers={"Range": "bytes=0-15"},
        )
        self.assertEqual(st, 206)
        self.assertEqual(len(partial), 16)
        self.assertTrue(hdrs.get("Content-Range", "").startswith("bytes 0-15/"))

        for evil in (str(self.outside_file), "../secret_outside.txt"):
            st, err, _ = _http_bytes(
                f"{self.base_url}/api/workspace-file?path={urllib.parse.quote(evil)}",
                token=self.token,
            )
            self.assertEqual(st, 403, f"Expected 403 for {evil}, got {st}: {err}")

        # 6. Cloud action before login returns 401 need_login; local action succeeds offline!
        st, need_login = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "image_edit", "source_path": str(self.hero_img), "prompt": "add crown"},
        )
        self.assertEqual(st, 401)
        self.assertEqual(need_login["error"]["code"], "need_login")

        st, slice_res = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "image_grid_slice", "source_path": str(self.hero_img), "rows": 2, "columns": 2, "fps": 12},
        )
        self.assertEqual(st, 200, slice_res)
        self.assertTrue(slice_res["ok"])
        atlas_path = Path(slice_res["atlas"]["atlas_path"])
        self.assertTrue(atlas_path.is_file())
        self.assertTrue(Path(slice_res["atlas"]["manifest_path"]).is_file())

        # Verify frame_sequence now appears in /api/assets with parent lineage to hero_img
        st, fs_scan = _http_json(f"{self.base_url}/api/assets?category=frame_sequence", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(len(fs_scan["assets"]), 1)
        self.assertEqual(fs_scan["assets"][0]["parents"][0]["parent_path"], str(self.hero_img.resolve()))

        # 7. Install credential context -> cloud action now succeeds seamlessly
        self._login_via_workbench()
        self._set_next_gateway_assets("image/png", self.hero_img.read_bytes())
        st, edit_res = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "image_edit", "source_path": str(self.hero_img), "prompt": "golden armor"},
        )
        self.assertEqual(st, 200, edit_res)
        self.assertTrue(edit_res["ok"])
        edited_path = Path(edit_res["created_files"][0])
        self.assertTrue(edited_path.is_file())

        conn = connect_db(self.state_db)
        try:
            dag = get_asset_lineage(conn, edited_path)
            self.assertEqual(dag["parents"][0]["parent_path"], str(self.hero_img.resolve()))
        finally:
            conn.close()

    def test_context_menu_reveal_and_move_to_trash_actions(self) -> None:
        from unittest import mock

        from src.asset_manager import actions as manager_actions

        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            if "trash" in argv[0] or "osascript" in argv[0]:
                Path(argv[-1]).unlink()  # simulate the OS moving the file to the trash
            return subprocess.CompletedProcess(argv, 0)

        def post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            return _http_json(f"{self.base_url}/api/manager/action", method="POST", token=self.token, body=body)

        with mock.patch.object(manager_actions.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(manager_actions.sys, "platform", "darwin"):
            # Reveal uses `open -R` with an argument list (no shell) and leaves the file alone.
            st, res = post({"action": "reveal_in_file_manager", "source_path": str(self.sample_wav)})
            self.assertEqual(st, 200, res)
            self.assertEqual(calls[-1], ["/usr/bin/open", "-R", str(self.sample_wav.resolve())])
            self.assertTrue(self.sample_wav.is_file())

            # Paths outside the workspace are rejected before any command runs.
            calls.clear()
            for evil in (str(self.outside_file), "../secret_outside.txt"):
                for action in ("reveal_in_file_manager", "move_to_trash"):
                    st, err = post({"action": action, "source_path": evil})
                    self.assertEqual(st, 403, err)
                    self.assertEqual(err["error"]["code"], "path_outside_workspace")
            st, err = post({"action": "move_to_trash", "source_path": str(self.sample_mp4), "manifest_path": str(self.outside_file)})
            self.assertEqual(st, 403, err)
            self.assertEqual(calls, [])
            self.assertTrue(self.outside_file.is_file())
            self.assertTrue(self.sample_mp4.is_file())

            # Trash moves the file (and a frame-sequence manifest) away; the scan no longer lists it.
            manifest = self.workspace / "video" / "clip.manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            st, res = post({"action": "move_to_trash", "source_path": str(self.sample_mp4), "manifest_path": str(manifest)})
            self.assertEqual(st, 200, res)
            self.assertEqual(res["result"]["trashed"], [str(self.sample_mp4.resolve()), str(manifest.resolve())])
            self.assertFalse(self.sample_mp4.exists())
            self.assertFalse(manifest.exists())

            st, scan = _http_json(f"{self.base_url}/api/assets?no_cache=1", token=self.token)
            self.assertEqual(st, 200)
            self.assertNotIn(str(self.sample_mp4.resolve()), [a["path"] for a in scan["assets"]])

            # A trash tool that fails without moving the file is an error, never a permanent delete.
            with mock.patch.object(manager_actions.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                st, err = post({"action": "move_to_trash", "source_path": str(self.sample_wav)})
            self.assertEqual(st, 400, err)
            self.assertEqual(err["error"]["code"], "trash_failed")
            self.assertTrue(self.sample_wav.is_file())

        st, sess = _http_json(f"{self.base_url}/api/session", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(sess["platform"], sys.platform)

    def test_all_cross_skill_actions_from_image_frame_sequence_video_3d_audio(self) -> None:
        self._login_via_workbench()

        # --- IMAGE ACTIONS ---
        # Local image tools: remove_background, trim, pad, resize, crop, convert
        for tool, extra in (
            ("remove_background", {"threshold": 25}),
            ("trim", {}),
            ("pad", {"padding": 8}),
            ("resize", {"width": 32, "height": 32}),
            ("crop", {"box": "0,0,32,32"}),
            ("convert", {"format": "webp"}),
        ):
            st, res = _http_json(
                f"{self.base_url}/api/manager/action",
                method="POST",
                token=self.token,
                body={"action": "image_local_tool", "source_path": str(self.hero_img), "tool": tool, **extra},
            )
            self.assertEqual(st, 200, f"tool={tool} failed: {res}")
            self.assertTrue(Path(res["created_files"][0]).is_file())

        # AI Sprite Sheet Generation (2-hop lineage: hero_img -> sheet -> atlas.png)
        self._set_next_gateway_assets("image/png", self.hero_img.read_bytes())
        st, ai_sheet = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={
                "action": "image_ai_spritesheet",
                "source_path": str(self.hero_img),
                "prompt": "knight walk cycle",
                "rows": 2,
                "columns": 2,
                "fps": 8,
            },
        )
        self.assertEqual(st, 200, ai_sheet)
        ai_atlas = Path(ai_sheet["atlas"]["atlas_path"])
        ai_raw_sheet = Path(ai_sheet["sheet_path"])
        self.assertTrue(ai_atlas.is_file())
        self.assertTrue(ai_raw_sheet.is_file())

        conn = connect_db(self.state_db)
        try:
            dag_atlas = get_asset_lineage(conn, ai_atlas)
            self.assertEqual(dag_atlas["parents"][0]["parent_path"], str(ai_raw_sheet.resolve()))
            ancestor_paths = {e["parent_path"] for e in dag_atlas["ancestors"]}
            self.assertIn(str(self.hero_img.resolve()), ancestor_paths)
        finally:
            conn.close()

        # Frame Sequence actions: repack + extract_frames
        st, repack_res = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "frame_sequence_repack", "source_path": str(ai_atlas), "rows": 2, "columns": 2, "fps": 12},
        )
        self.assertEqual(st, 200, repack_res)
        self.assertTrue(Path(repack_res["atlas"]["atlas_path"]).is_file())

        st, extract_res = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "frame_sequence_extract_frames", "source_path": str(ai_atlas)},
        )
        self.assertEqual(st, 200, extract_res)
        self.assertEqual(len(extract_res["created_files"]), 4)

        # Image -> Video
        self._set_next_gateway_assets("video/mp4", self.sample_mp4.read_bytes())
        st, img2vid = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "image_to_video", "source_path": str(self.hero_img), "prompt": "hero charges forward"},
        )
        self.assertEqual(st, 200, img2vid)
        derived_mp4 = Path(img2vid["created_files"][0])
        self.assertTrue(derived_mp4.is_file())

        # Image -> 3D
        self._set_next_gateway_assets("model/gltf-binary", self.sample_glb.read_bytes())
        st, img23d = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "image_to_3d", "source_path": str(self.hero_img), "output_format": "glb"},
        )
        self.assertEqual(st, 200, img23d)
        derived_glb = Path(img23d["created_files"][0])
        self.assertTrue(derived_glb.is_file())

        # --- VIDEO ACTIONS ---
        self._set_next_gateway_assets("video/mp4", self.sample_mp4.read_bytes())
        st, vid_edit = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "video_edit", "source_path": str(derived_mp4), "prompt": "neon lighting"},
        )
        self.assertEqual(st, 200, vid_edit)

        for v_act, extra in (
            ("video_trim", {"start": 0.0, "duration": 0.5}),
            ("video_convert", {"format": "gif"}),
            ("video_mux", {"audio_path": str(self.sample_wav)}),
            ("video_spritesheet", {"fps": 5}),
        ):
            st, v_res = _http_json(
                f"{self.base_url}/api/manager/action",
                method="POST",
                token=self.token,
                body={"action": v_act, "source_path": str(self.sample_mp4), **extra},
            )
            self.assertEqual(st, 200, f"video action {v_act} failed: {v_res}")
            self.assertTrue(Path(v_res["created_files"][0]).is_file())

        # Verify multi-parent lineage on video_mux
        conn = connect_db(self.state_db)
        try:
            st_scan, vid_scan = _http_json(f"{self.base_url}/api/assets?category=video", token=self.token)
            self.assertEqual(st_scan, 200)
            muxed = [a for a in vid_scan["assets"] if a.get("operation") == "mux"]
            self.assertEqual(len(muxed), 1)
            mux_parents = {e["parent_path"] for e in muxed[0]["parents"]}
            self.assertEqual(mux_parents, {str(self.sample_mp4.resolve()), str(self.sample_wav.resolve())})
        finally:
            conn.close()

        # --- 3D ACTIONS ---
        # Animation takes a rig task, not an uploaded file: animate the file the rig action delivered.
        rigged_glb = derived_glb
        for m_act, extra in (
            ("model3d_rig_check", {}),
            ("model3d_rig", {"rig_type": "biped"}),
            ("model3d_animate", {"animation": "run"}),
            ("model3d_convert", {"output_format": "obj"}),
        ):
            self._set_next_gateway_assets("model/gltf-binary", self.sample_glb.read_bytes())
            st, m_res = _http_json(
                f"{self.base_url}/api/manager/action",
                method="POST",
                token=self.token,
                body={"action": m_act,
                      "source_path": str(rigged_glb if m_act == "model3d_animate" else derived_glb), **extra},
            )
            self.assertEqual(st, 200, f"3d action {m_act} failed: {m_res}")
            if m_act == "model3d_rig_check":
                self.assertEqual(m_res["created_files"], [])
                self.assertTrue(m_res["result"]["data"]["riggable"])
            else:
                self.assertTrue(Path(m_res["created_files"][0]).is_file())
            if m_act == "model3d_rig":
                rigged_glb = Path(m_res["created_files"][0])

        # An unrigged upload is still refused for animation, with the rig-first instruction.
        st, refused = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "model3d_animate", "source_path": str(self.sample_glb), "animation": "run"},
        )
        self.assertEqual(st, 400, refused)
        self.assertEqual(refused["error"]["code"], "file_source_not_supported")

        st, m_info = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "model3d_info", "source_path": str(self.sample_glb)},
        )
        self.assertEqual(st, 200, m_info)
        self.assertEqual(m_info["result"]["model"]["format"], "glb")

        st, m_val = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "model3d_validate", "source_path": str(self.sample_glb), "max_faces": 1000},
        )
        self.assertEqual(st, 200, m_val)
        self.assertIn("valid", m_val["result"])

        st, m_ext = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "model3d_extract_textures", "source_path": str(self.sample_glb)},
        )
        self.assertEqual(st, 200, m_ext)

        # --- AUDIO ACTIONS ---
        self._set_next_gateway_assets("audio/wav", self.sample_wav.read_bytes())
        st, aud_speech = _http_json(
            f"{self.base_url}/api/manager/action",
            method="POST",
            token=self.token,
            body={"action": "audio_speech", "source_path": str(self.sample_wav), "text": "For the realm!"},
        )
        self.assertEqual(st, 200, aud_speech)
        speech_file = Path(aud_speech["created_files"][0])
        self.assertTrue(speech_file.is_file())

        for a_act, extra in (
            ("audio_trim", {"start": 0.0, "duration": 0.3}),
            ("audio_fade", {"fade_in": 0.05, "fade_out": 0.05}),
            ("audio_normalize", {"target_lufs": -14.0}),
            ("audio_convert", {"format": "mp3"}),
        ):
            st, a_res = _http_json(
                f"{self.base_url}/api/manager/action",
                method="POST",
                token=self.token,
                body={"action": a_act, "source_path": str(self.sample_wav), **extra},
            )
            self.assertEqual(st, 200, f"audio action {a_act} failed: {a_res}")
            self.assertTrue(Path(a_res["created_files"][0]).is_file())

    def test_embedded_sub_workbench_session_source_submit_keep_alive_and_confinement(self) -> None:
        self._login_via_workbench()

        # 1. /api/session with explicit source_path confined to workspace
        q_hero = urllib.parse.quote(str(self.hero_img))
        st, sess = _http_json(
            f"{self.base_url}/api/session?source_path={q_hero}&mode=region_edit",
            token=self.token,
        )
        self.assertEqual(st, 200)
        self.assertEqual(sess["mode"], "region_edit")
        self.assertEqual(sess["source_path"], str(self.hero_img.resolve()))
        self.assertIn("path=", sess["source_url"])

        # Path outside workspace rejected with 403
        st, err_sess = _http_json(
            f"{self.base_url}/api/session?source_path={urllib.parse.quote(str(self.outside_file))}",
            token=self.token,
        )
        self.assertEqual(st, 403, err_sess)

        st, err_src, _ = _http_bytes(
            f"{self.base_url}/api/source?path={urllib.parse.quote(str(self.outside_file))}",
            token=self.token,
        )
        self.assertEqual(st, 403)

        # 2. Embedded region_edit /api/submit with keep_alive
        self._set_next_gateway_assets("image/png", self.hero_img.read_bytes())
        mask_img = PIL.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        for y in range(16, 48):
            for x in range(16, 48):
                mask_img.putpixel((x, y), (255, 255, 255, 255))
        mask_buf = io.BytesIO()
        mask_img.save(mask_buf, format="PNG")
        mask_data_url = "data:image/png;base64," + base64.b64encode(mask_buf.getvalue()).decode("ascii")

        st, reg_res = _http_json(
            f"{self.base_url}/api/submit",
            method="POST",
            token=self.token,
            body={
                "mode": "region_edit",
                "source_path": str(self.hero_img),
                "keep_alive": True,
                "mask": mask_data_url,
                "annotation_preview": _data_url_png((255, 0, 0, 255)),
                "annotations": [{"id": "m1", "type": "rectangle", "instruction": "glowing visor"}],
                "prompt": "sci-fi helmet",
                "model": "gpt-image-2.5-sunburst",
            },
        )
        self.assertEqual(st, 200, reg_res)
        self.assertFalse(self.server.shutdown_event.is_set())
        reg_file = Path(reg_res["result"]["files"][0])
        self.assertTrue(reg_file.is_relative_to(self.workspace.resolve()))

        # 3. Embedded image_tools /api/submit with keep_alive
        st, tool_res = _http_json(
            f"{self.base_url}/api/submit",
            method="POST",
            token=self.token,
            body={
                "mode": "tools",
                "source_path": str(self.hero_img),
                "keep_alive": True,
                "image_data": _data_url_png((0, 255, 0, 255)),
                "recipe": "remove_background",
            },
        )
        self.assertEqual(st, 200, tool_res)
        self.assertFalse(self.server.shutdown_event.is_set())

        # 4. Embedded video_frame_sequence & model3d_render_sequence /api/submit
        dummy_manifest = {
            "frames": {"frame-0000.png": {"frame": {"x": 0, "y": 0, "w": 32, "h": 32}}},
            "meta": {"image": "atlas.png", "fps": 10},
        }
        st, vfs_res = _http_json(
            f"{self.base_url}/api/submit",
            method="POST",
            token=self.token,
            body={
                "mode": "video_frame_sequence",
                "source_path": str(self.sample_mp4),
                "keep_alive": True,
                "atlas": _data_url_png((20, 20, 200, 255), size=(32, 32)),
                "manifest": dummy_manifest,
            },
        )
        self.assertEqual(st, 200, vfs_res)
        atlas_v_path = Path(vfs_res["result"]["atlas"]["path"])
        self.assertTrue(atlas_v_path.is_file())

        st, mfs_res = _http_json(
            f"{self.base_url}/api/submit",
            method="POST",
            token=self.token,
            body={
                "mode": "model3d_render_sequence",
                "source_path": str(self.sample_glb),
                "keep_alive": True,
                "atlas": _data_url_png((200, 20, 200, 255), size=(32, 32)),
                "manifest": dummy_manifest,
            },
        )
        self.assertEqual(st, 200, mfs_res)
        atlas_3d_path = Path(mfs_res["result"]["atlas"]["path"])
        self.assertTrue(atlas_3d_path.is_file())
        self.assertNotEqual(atlas_v_path, atlas_3d_path)

        # Verify live refresh of /api/assets shows all embedded outputs with lineage
        st, live_assets = _http_json(f"{self.base_url}/api/assets?no_cache=1", token=self.token)
        self.assertEqual(st, 200)
        by_path = {a["path"]: a for a in live_assets["assets"]}
        self.assertIn(str(reg_file.resolve()), by_path)
        self.assertEqual(by_path[str(atlas_v_path.resolve())]["category"], "frame_sequence")
        self.assertEqual(by_path[str(atlas_v_path.resolve())]["parents"][0]["parent_path"], str(self.sample_mp4.resolve()))
        self.assertEqual(by_path[str(atlas_3d_path.resolve())]["parents"][0]["parent_path"], str(self.sample_glb.resolve()))


class TestStandalonePackagedAssetManagerWorkbench(unittest.TestCase):
    def test_extracted_dist_zip_ui_runs_standalone_outside_repo(self) -> None:
        from src.build_skills import package_skill

        zip_path, _ = package_skill("asset_manager")
        temp_dir = Path(tempfile.mkdtemp(prefix="asx-t05-dist-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))

        skills_dir = temp_dir / "isolated_skills"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(skills_dir)

        workspace = temp_dir / "game_proj"
        workspace.mkdir()
        hero = workspace / "knight.png"
        PIL.Image.new("RGBA", (64, 64), (120, 40, 200, 255)).save(hero)

        gw_server, gw_url, _ = run_fake_gateway()
        self.addCleanup(gw_server.shutdown)
        skill_dir = temp_dir / "skill"
        login_server, login_url, _ = run_fake_sagein_server(gw_url)
        self.addCleanup(login_server.shutdown)

        mp4_path = temp_dir / "vid.mp4"
        make_test_mp4(mp4_path)
        gw_server.asset_data["task_1:0"] = mp4_path.read_bytes()
        gw_server.asset_mimes["task_1:0"] = "video/mp4"

        env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(temp_dir / "state.db"),
            "ASYNX_CONFIG_PATH": str(temp_dir / "config.json"),
            "ASYNX_CACHE_PATH": str(temp_dir / "cache" / "models.json"),
            "PLAIERS_SKILL_CONTEXT": str(skill_dir / ".plaiers-context.json"),
            "ASYNX_NO_OPEN_BROWSER": "1",
        }
        env.pop("ASYNX_PARTNER_TOKEN", None)
        env.pop("ASYNX_BASE_URL", None)

        image_zip, _ = package_skill("image")
        with zipfile.ZipFile(image_zip) as zf:
            zf.extractall(skills_dir)
        # Project browsing and local editing must start without cloud credentials.
        asynx_py = skills_dir / "plaiers-asynx-image-pro" / "scripts" / "asynx.py"
        proc = subprocess.Popen(
            [sys.executable, str(asynx_py), "project", "open", "--root", str(workspace), "--no-open"],
            cwd=str(temp_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _cleanup_proc() -> None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        self.addCleanup(_cleanup_proc)

        # Read URL + token from stderr
        url_line = ""
        deadline = time.time() + 10.0
        while time.time() < deadline:
            line = proc.stderr.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            if "Local UI Workbench available at:" in line:
                url_line = line.strip()
                break

        m = re.search(r"(http://127\.0\.0\.1:\d+)/\?token=([a-f0-9]+)", url_line)
        self.assertIsNotNone(m, f"Workbench URL not found in stderr: {url_line}")
        base_url = m.group(1)
        token = m.group(2)

        # Verify standalone UI & embedded sub-workbench pages serve cleanly
        for rel in ("/", "/account-bar.js", "/model3d-loader.js", "/image/index.html", "/video/index.html", "/3d/index.html"):
            st, body, _ = _http_bytes(f"{base_url}{rel}")
            self.assertEqual(st, 200, f"Standalone failed serving {rel}")
            self.assertGreater(len(body), 100)

        # Malformed numeric action params return HTTP 400 invalid_parameters (not 500)
        st, bad_num = _http_json(
            f"{base_url}/api/manager/action",
            method="POST",
            token=token,
            body={"action": "image_grid_slice", "source_path": str(hero), "rows": "not-an-int", "columns": 2},
        )
        self.assertEqual(st, 400, bad_num)
        self.assertEqual(bad_num["error"]["code"], "invalid_parameters")

        st, bad_bool = _http_json(
            f"{base_url}/api/manager/action",
            method="POST",
            token=token,
            body={"action": "image_grid_slice", "source_path": str(hero), "rows": True, "fps": "nan"},
        )
        self.assertEqual(st, 400, bad_bool)
        self.assertEqual(bad_bool["error"]["code"], "invalid_parameters")

        # Offline scan + local grid slice
        st, scan1 = _http_json(f"{base_url}/api/assets", token=token)
        self.assertEqual(st, 200)
        self.assertEqual(scan1["counts"]["total"], 1)

        st, slice_res = _http_json(
            f"{base_url}/api/manager/action",
            method="POST",
            token=token,
            body={"action": "image_grid_slice", "source_path": str(hero), "rows": 2, "columns": 2, "fps": 10},
        )
        self.assertEqual(st, 200, slice_res)

        # Install the credential context & run cloud image_to_video in standalone process
        install_fake_context(skill_dir, login_url)

        st, vid_res = _http_json(
            f"{base_url}/api/manager/action",
            method="POST",
            token=token,
            body={"action": "image_to_video", "source_path": str(hero), "prompt": "knight walks"},
        )
        self.assertEqual(st, 200, vid_res)

        st, scan2 = _http_json(f"{base_url}/api/assets?no_cache=1", token=token)
        self.assertEqual(st, 200)
        self.assertEqual(scan2["counts"]["total"], 3)
        self.assertEqual(scan2["counts"]["by_category"]["frame_sequence"], 1)
        self.assertEqual(scan2["counts"]["by_category"]["video"], 1)

        st, lin = _http_json(
            f"{base_url}/api/lineage?target={urllib.parse.quote(str(hero))}",
            token=token,
        )
        self.assertEqual(st, 200)
        self.assertEqual(len(lin["children"]), 2)


if __name__ == "__main__":
    unittest.main()
