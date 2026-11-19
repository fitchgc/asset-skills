"""Issue 05 (parts that do not wait on Q1/Q2): slim core packages + one optional workbench package.

Core packages (image/audio/video/3d) carry generation, query and download only. Browser workbenches
and local post-processing live once in the workbench package (asset-manager, ADR-0007) and are
loaded on demand; without it, those commands return ``workbench_not_installed``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import build_skills  # noqa: E402

CORE = {"image": "asynx-image-pro", "audio": "asynx-audio", "video": "asynx-video", "3d": "asynx-3d"}
WORKBENCH = "asynx-asset-manager"
WORKBENCH_ONLY = {"ui_server.py", "thumb_cache.py", "local_images.py", "local_audio.py", "local_video.py", "local_model3d.py"}
GLTF = {"asset": {"version": "2.0"}, "meshes": [], "accessors": [], "materials": []}


def build_all() -> None:
    for key in (*CORE, "asset_manager"):
        build_skills.build_skill(key)


class PackageContentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        build_all()

    def test_core_packages_are_slim_text_only(self) -> None:
        for name in CORE.values():
            root = REPO_ROOT / "skills" / name
            asxlib = root / "scripts" / "asxlib"
            self.assertFalse((asxlib / "ui").exists(), name)
            self.assertEqual(sorted(WORKBENCH_ONLY & {p.name for p in asxlib.iterdir()}), [], name)
            self.assertEqual([p for p in root.rglob("*") if p.suffix in {".ttf", ".otf", ".woff", ".woff2"}], [], name)
            self.assertTrue((asxlib / "workbench.py").is_file(), name)
            stats = build_skills.check_package(root)
            self.assertLessEqual(stats["files"], 200)
            self.assertLessEqual(stats["bytes"], 4 * 1024 * 1024)

    def test_workbench_package_holds_ui_and_local_processing_once(self) -> None:
        root = REPO_ROOT / "skills" / WORKBENCH
        asxlib = root / "scripts" / "asxlib"
        self.assertTrue(WORKBENCH_ONLY <= {p.name for p in asxlib.iterdir()})
        for page in ("index.html", "image/index.html", "video/index.html", "3d/index.html", "plaiers.css"):
            self.assertTrue((asxlib / "ui" / page).is_file(), page)
        self.assertFalse((asxlib / "ui" / "fonts").exists())
        css = (asxlib / "ui" / "plaiers.css").read_text(encoding="utf-8")
        self.assertNotIn("@font-face", css)
        self.assertRegex(css, r"--en:[^;]*Impact[^;]*sans-serif")
        build_skills.check_package(root)
        self.assertEqual(list(REPO_ROOT.glob("src/ui/**/*.ttf")), [])

    def test_lockstep_version(self) -> None:
        versions = set()
        for name in (*CORE.values(), WORKBENCH):
            text = (REPO_ROOT / "skills" / name / "scripts" / "asxlib" / "constants.py").read_text(encoding="utf-8")
            versions.add(re.search(r'^VERSION = "([^"]+)"', text, re.MULTILINE).group(1))
        self.assertEqual(len(versions), 1)


class BuildChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_non_utf8_file_fails_the_build(self) -> None:
        (self.temp / "SKILL.md").write_text("# ok\n", encoding="utf-8")
        (self.temp / "font.ttf").write_bytes(b"\x00\x01\xff\xfe binary")
        with self.assertRaises(build_skills.PackageCheckError) as ctx:
            build_skills.check_package(self.temp)
        self.assertIn("non-UTF-8 file: font.ttf", str(ctx.exception))

    def test_limits_fail_the_build(self) -> None:
        for i in range(201):
            (self.temp / f"f{i}.md").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(build_skills.PackageCheckError, "201 files > 200"):
            build_skills.check_package(self.temp)
        shutil.rmtree(self.temp)
        self.temp.mkdir()
        (self.temp / "big.md").write_text("x" * (4 * 1024 * 1024 + 1), encoding="utf-8")
        with self.assertRaisesRegex(build_skills.PackageCheckError, "bytes > 4194304"):
            build_skills.check_package(self.temp)

    def test_ds_store_is_pruned(self) -> None:
        (self.temp / "sub").mkdir()
        for path in (self.temp / ".DS_Store", self.temp / "sub" / ".DS_Store"):
            path.write_bytes(b"\x00\x00\x00\x01Bud1")
        build_skills._prune_cache_artifacts(self.temp)
        self.assertEqual(list(self.temp.rglob(".DS_Store")), [])
        build_skills.check_package(self.temp)


class WorkbenchDiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        build_all()

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.skills = self.temp / "skills"
        self.core = self.skills / "plaiers-asynx-3d"
        shutil.copytree(REPO_ROOT / "skills" / "asynx-3d", self.core, ignore=shutil.ignore_patterns("__pycache__"))
        self.model = self.temp / "m.gltf"
        self.model.write_text(json.dumps(GLTF), encoding="utf-8")
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.temp / "home"),
            "ASYNX_STATE_PATH": str(self.temp / "state.db"),
            "ASYNX_CONFIG_PATH": str(self.temp / "config.json"),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def cli(self, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        proc = subprocess.run([sys.executable, "-S", str(self.core / "scripts" / "asynx.py"), *args], capture_output=True,
                              text=True, env={**self.env, **(env or {})}, cwd=self.temp, timeout=60)
        return proc.returncode, json.loads(proc.stdout)

    def add_workbench(self, name: str = "plaiers-asynx-asset-manager") -> Path:
        target = self.skills / name
        shutil.copytree(REPO_ROOT / "skills" / WORKBENCH, target, ignore=shutil.ignore_patterns("__pycache__"))
        return target

    def test_without_workbench_local_and_ui_commands_say_not_installed(self) -> None:
        for args in (("model3d", "info", str(self.model)), ("ui", "viewer", "--model-file", str(self.model), "--no-open")):
            code, out = self.cli(*args)
            self.assertEqual(code, 2, out)
            self.assertEqual(out["error"]["code"], "workbench_not_installed")
            self.assertEqual(out["error"]["details"]["get_skill_id"], "plaiers/asynx-asset-manager")
            self.assertIn("get_skill", out["error"]["message"])
        # The main flow is unaffected.
        code, out = self.cli("config", "status")
        self.assertEqual(code, 0, out)

    def test_sibling_workbench_is_loaded_on_demand(self) -> None:
        self.add_workbench()
        code, out = self.cli("model3d", "info", str(self.model))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["model"]["format"], "gltf")

    def test_repo_install_name_and_env_override(self) -> None:
        self.add_workbench("asynx-asset-manager")
        self.assertEqual(self.cli("model3d", "info", str(self.model))[0], 0)
        elsewhere = self.temp / "elsewhere" / "wb"
        shutil.move(str(self.skills / "asynx-asset-manager"), elsewhere)
        self.assertEqual(self.cli("model3d", "info", str(self.model))[1]["error"]["code"], "workbench_not_installed")
        code, out = self.cli("model3d", "info", str(self.model), env={"ASYNX_WORKBENCH_DIR": str(elsewhere)})
        self.assertEqual(code, 0, out)

    def test_version_mismatch_is_reported_only_when_used(self) -> None:
        wb = self.add_workbench()
        constants = wb / "scripts" / "asxlib" / "constants.py"
        constants.write_text(re.sub(r'^VERSION = "[^"]+"', 'VERSION = "0.0.1"', constants.read_text(encoding="utf-8"), flags=re.MULTILINE),
                             encoding="utf-8")
        self.assertEqual(self.cli("config", "status")[0], 0)
        code, out = self.cli("model3d", "info", str(self.model))
        self.assertEqual(code, 2, out)
        self.assertEqual(out["error"]["code"], "workbench_version_mismatch")
        self.assertEqual(out["error"]["details"]["workbench_version"], "0.0.1")


if __name__ == "__main__":
    unittest.main()
