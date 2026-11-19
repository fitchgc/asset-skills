"""Issue 06: update status comes from the Plaiers skill registry; the package never rewrites itself.

A loopback fake registry serves ``/api/skills/get``. The installed identity (id + pinned version) and
the site come from the installer-written ``.plaiers-context.json``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SKILL_ID = "plaiers/asynx-image-pro"
V1 = hashlib.sha256(b"v1").hexdigest()
V2 = hashlib.sha256(b"v2").hexdigest()
SKILLS = ("asynx-image-pro", "asynx-audio", "asynx-video", "asynx-3d", "asynx-asset-manager")


class FakeRegistry:
    """versions: version -> 'published' | 'withdrawn'; current: version returned without ?version."""

    def __init__(self) -> None:
        self.versions: dict[str, str] = {V1: "published"}
        self.current: str | None = V1
        self.enabled = True
        self.requests: list[str] = []
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                registry.requests.append(self.path)
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                status, body = registry.answer(parts.path, query)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, path: str, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if not self.enabled or path != "/api/skills/get" or query.get("id") != SKILL_ID:
            return 404, {"error": "Skill not available"}
        version = query.get("version", self.current)
        if version is None or version not in self.versions:
            return 404, {"error": "Skill version not found"}
        if self.versions[version] != "published":
            return 410, {"error": "Skill version withdrawn"}
        return 200, {"id": SKILL_ID, "version": version, "kind": "capability"}

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class RegistryUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill

        build_skill("image")

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.registry = FakeRegistry()
        # A copy of the built skill, installed the way the Plaiers installer leaves it.
        self.skill = self.temp / "skills" / "plaiers-asynx-image-pro"
        shutil.copytree(REPO_ROOT / "skills" / "asynx-image-pro", self.skill, ignore=shutil.ignore_patterns("__pycache__"))
        self.bridge_dir = self.temp / "plaiers-community" / "scripts"
        self.write_context(self.registry.origin)
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.temp / "home"),
            "ASYNX_STATE_PATH": str(self.temp / "state.db"),
            "ASYNX_CONFIG_PATH": str(self.temp / "config.json"),
        }

    def tearDown(self) -> None:
        self.registry.close()
        shutil.rmtree(self.temp, ignore_errors=True)

    def write_context(self, origin: str, **extra: Any) -> None:
        context = {
            "schema_version": 1,
            "origin": origin,
            "agent_id": "agent-a",
            "jwt_command": ["node", str(self.bridge_dir / "get-skill-jwt.mjs"), "--agent-id", "agent-a",
                            "--origin", origin, "--config-dir", str(self.temp / "cfg dir")],
            "skill_id": SKILL_ID,
            "skill_version": V1,
        }
        context.update(extra)
        context = {k: v for k, v in context.items() if v is not None}
        (self.skill / ".plaiers-context.json").write_text(json.dumps(context), encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {p.relative_to(self.skill).as_posix(): p.read_bytes() for p in sorted(self.skill.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts}

    def cli(self, *args: str) -> tuple[int, dict[str, Any]]:
        proc = subprocess.run([sys.executable, str(self.skill / "scripts" / "asynx.py"), *args],
                              capture_output=True, text=True, env=self.env, cwd=self.temp, timeout=60)
        return proc.returncode, json.loads(proc.stdout)

    def test_up_to_date(self) -> None:
        code, out = self.cli("update", "status")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            {k: out[k] for k in ("installed_version", "current_version", "withdrawn", "update_available")},
            {"installed_version": V1, "current_version": V1, "withdrawn": False, "update_available": False},
        )
        self.assertNotIn("update", out)
        self.assertEqual(out["origin"], self.registry.origin)

    def test_newer_version_gives_installer_command_and_changes_nothing(self) -> None:
        self.registry.versions[V2] = "published"
        self.registry.current = V2
        before = self.snapshot()
        code, out = self.cli("update", "check")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["installed_version"], out["current_version"], out["withdrawn"], out["update_available"]),
                         (V1, V2, False, True))
        update = out["update"]
        self.assertTrue(update["requires_user_consent"])
        self.assertEqual(update["install_command"], [
            "node", str(self.bridge_dir / "install-capability-skill.mjs"), "install", "--id", SKILL_ID, "--version", V2,
            "--agent-id", "agent-a", "--origin", self.registry.origin, "--config-dir", str(self.temp / "cfg dir"),
        ])
        self.assertIn("'", update["install_command_shell"], "paths with spaces are shell-quoted")
        self.assertEqual(self.snapshot(), before)

    def test_withdrawn_installed_version_is_reported_and_kept(self) -> None:
        self.registry.versions = {V1: "withdrawn", V2: "published"}
        self.registry.current = V2
        before = self.snapshot()
        code, out = self.cli("update", "status")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["withdrawn"])
        self.assertFalse(out["installed_available"])
        self.assertEqual(out["current_version"], V2)
        self.assertIn("不会被自动删除", out["withdrawn_notice"])
        self.assertEqual(self.snapshot(), before)
        # Withdrawn with no published replacement: still reported, still kept.
        self.registry.versions = {V1: "withdrawn"}
        self.registry.current = None
        code, out = self.cli("update", "status")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["withdrawn"], out["current_version"], out["update_available"]), (True, None, False))
        self.assertEqual(self.snapshot(), before)

    def test_registry_disabled(self) -> None:
        self.registry.enabled = False
        code, out = self.cli("update", "status")
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "registry_disabled")

    def test_registry_unreachable(self) -> None:
        origin = self.registry.origin
        self.registry.close()
        self.registry = FakeRegistry()  # keep tearDown happy; the context still points at the closed port
        self.write_context(origin)
        code, out = self.cli("update", "status")
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "registry_unavailable")

    def test_non_registry_install_and_unsupported_origin(self) -> None:
        self.write_context(self.registry.origin, skill_id=None, skill_version=None)
        code, out = self.cli("update", "status")
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "not_registry_install")
        self.write_context("https://plaiers.ai")
        code, out = self.cli("update", "status")
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "registry_unsupported_origin")
        self.assertEqual(self.registry.requests, [])

    def test_apply_and_rollback_are_removed(self) -> None:
        self.registry.versions[V2] = "published"
        self.registry.current = V2
        before = self.snapshot()
        for command in ("apply", "rollback"):
            code, out = self.cli("update", command)
            self.assertNotEqual(code, 0)
            self.assertEqual(out["error"]["code"], "update_apply_removed")
            self.assertIn("install-capability-skill.mjs", out["error"]["message"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.registry.requests, [], "apply/rollback make no network requests")

    def test_ordinary_commands_never_check_for_updates(self) -> None:
        self.cli("doctor")
        self.cli("config", "status")
        self.cli("runtime", "status")
        self.assertEqual(self.registry.requests, [])
        code, out = self.cli("doctor", "--updates")
        self.assertEqual(out["updates"]["installed_version"], V1)
        self.assertEqual(len(self.registry.requests), 2)


class NoGitHubUpdaterTest(unittest.TestCase):
    def test_packages_have_no_github_or_self_update_code(self) -> None:
        from src.build_skills import build_skill

        for modality in ("image", "audio", "video", "3d", "asset_manager"):
            build_skill(modality)
        for skill in SKILLS:
            scripts = REPO_ROOT / "skills" / skill / "scripts"
            for path in scripts.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for needle in ("github.com", "api.github.com", "releases/latest", "automatic_update_notice", "replace_installations"):
                    self.assertNotIn(needle, text, f"{path}: {needle}")


if __name__ == "__main__":
    unittest.main()
