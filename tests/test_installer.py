from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]

import install
from tests.fake_gateway import run_fake_gateway


class TestInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_home = Path(tempfile.mkdtemp(prefix="asx-install-home-"))
        self.config_dir = self.temp_home / ".config" / "asynx"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self.config_file.write_text(
            json.dumps({"token": "pt_test_installer_0000"}),
            encoding="utf-8",
        )
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)

    def tearDown(self) -> None:
        os.environ.pop("ASYNX_CONFIG_PATH", None)
        shutil.rmtree(self.temp_home, ignore_errors=True)

    def test_resolve_skill_names(self) -> None:
        all_skills = install._resolve_skill_names("all")
        self.assertEqual(
            all_skills,
            [
                "asynx-image-pro",
                "asynx-audio",
                "asynx-video",
                "asynx-3d",
                "asynx-asset-manager",
            ],
        )

        subset = install._resolve_skill_names("image, 3d, asset-manager")
        self.assertEqual(subset, ["asynx-image-pro", "asynx-3d", "asynx-asset-manager"])

        mgr_aliases = install._resolve_skill_names("manager, assets")
        self.assertEqual(mgr_aliases, ["asynx-asset-manager"])

        audio_subset = install._resolve_skill_names("snd,video")
        self.assertEqual(audio_subset, ["asynx-audio", "asynx-video"])

        with self.assertRaises(ValueError):
            install._resolve_skill_names("invalid-skill-name")

    def test_target_detection(self) -> None:
        # None existing
        self.assertEqual(install._detected_targets(self.temp_home), [])

        # Codex exists (.agents)
        (self.temp_home / ".agents").mkdir()
        self.assertEqual(install._detected_targets(self.temp_home), ["codex"])

        # Claude exists
        (self.temp_home / ".claude").mkdir()
        self.assertEqual(
            install._detected_targets(self.temp_home),
            ["codex", "claude"],
        )

    def test_legacy_asx_check(self) -> None:
        legacy_dir = self.temp_home / ".agents" / "skills" / "asx"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(RuntimeError) as ctx:
            install._check_legacy_installations(self.temp_home, ["codex"])
        self.assertIn("检测到旧版 asx", str(ctx.exception))

    def test_install_and_uninstall_lifecycle(self) -> None:
        (self.temp_home / ".claude").mkdir(parents=True, exist_ok=True)

        # Install audio, image, and asset-manager into claude target without network dependencies
        ret = install.run(
            [
                "--target",
                "claude",
                "--skill",
                "audio,image,asset-manager",
                "--skip-config",
                "--no-verify",
            ],
            home=self.temp_home,
            install_dependencies=False,
        )
        self.assertEqual(ret, 0)

        claude_skills = self.temp_home / ".claude" / "skills"
        image_dir = claude_skills / "asynx-image-pro"
        audio_dir = claude_skills / "asynx-audio"
        manager_dir = claude_skills / "asynx-asset-manager"
        video_dir = claude_skills / "asynx-video"

        self.assertTrue(image_dir.is_dir())
        self.assertTrue(audio_dir.is_dir())
        self.assertTrue(manager_dir.is_dir())
        self.assertFalse(video_dir.exists())

        # Check installed script permissions and lock
        script = image_dir / "scripts" / "asynx.py"
        self.assertTrue(script.exists())
        self.assertTrue((manager_dir / "scripts" / "asynx.py").exists())
        self.assertTrue((manager_dir / "scripts" / "asxlib" / "scanner.py").exists())
        self.assertTrue((manager_dir / "scripts" / "asxlib" / "ui" / "index.html").exists())
        self.assertTrue((claude_skills / ".asx-runtime.lock").exists())
        self.assertTrue((image_dir / ".asx-install.json").exists())
        self.assertTrue((manager_dir / ".asx-install.json").exists())

        # Uninstall image only
        ret_uninst = install.run(
            [
                "--uninstall",
                "--yes",
                "--target",
                "claude",
                "--skill",
                "image",
            ],
            home=self.temp_home,
        )
        self.assertEqual(ret_uninst, 0)
        self.assertFalse(image_dir.exists())
        self.assertTrue(audio_dir.exists())
        self.assertTrue(manager_dir.exists())

        # Uninstall remaining
        ret_uninst_all = install.run(
            ["--uninstall", "--yes", "--target", "claude"],
            home=self.temp_home,
        )
        self.assertEqual(ret_uninst_all, 0)
        self.assertFalse(audio_dir.exists())
        self.assertFalse(manager_dir.exists())

    def test_verify_with_fake_gateway(self) -> None:
        from tests.fake_sagein_server import run_fake_sagein_server

        server, url, _ = run_fake_gateway()
        self.addCleanup(server.shutdown)
        cred = self.temp_home / "cred.json"
        sagein, origin, _ = run_fake_sagein_server(url, cred)
        self.addCleanup(sagein.shutdown)
        self.config_file.write_text("{}", encoding="utf-8")
        os.environ["SAGEIN_COMMUNITY_CREDENTIAL_FILE"] = str(cred)
        os.environ["SAGEIN_COMMUNITY_ORIGIN"] = origin
        self.addCleanup(os.environ.pop, "SAGEIN_COMMUNITY_CREDENTIAL_FILE", None)
        self.addCleanup(os.environ.pop, "SAGEIN_COMMUNITY_ORIGIN", None)

        (self.temp_home / ".agents").mkdir(parents=True, exist_ok=True)
        ret = install.run(
            [
                "--target",
                "codex",
                "--skill",
                "audio",
            ],
            home=self.temp_home,
            install_dependencies=False,
        )
        self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
