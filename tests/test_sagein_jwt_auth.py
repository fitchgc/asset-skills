"""JWT only through the community get-skill-jwt.mjs bridge recorded in the local credential context.

The fake bridge (tests/fake_jwt_bridge.py) mirrors the real bridge contract; these tests assert the
Asset Skill side: it runs exactly the recorded argv, keeps no JWT cache, never touches the PAT, and never
falls back to any other credential source or origin.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
from tests.interpreter_pillow import add_interpreter_pillow  # noqa: E402

add_interpreter_pillow()

from shared.asxlib import constants
from shared.asxlib.auth import decode_jwt, load_auth_session
from shared.asxlib.config import config_status, doctor, load_client, load_credentials
from shared.asxlib.errors import AsxError
from tests.fake_gateway import run_fake_gateway
from tests.fake_sagein_server import (
    BRIDGE_SCRIPT,
    PAT,
    install_fake_context,
    make_test_jwt,
    run_fake_sagein_server,
    write_binding,
)

PAT_B = "sagein_pat_" + "b" * 64
PAT_C = "sagein_pat_" + "c" * 64
_ENV_KEYS = (
    "ASYNX_CONFIG_PATH", "ASYNX_LOG_PATH", "ASYNX_PARTNER_TOKEN", "ASYNX_BASE_URL", "PLAIERS_SKILL_CONTEXT",
    "SAGEIN_COMMUNITY_TOKEN", "SAGEIN_COMMUNITY_ORIGIN", "SAGEIN_COMMUNITY_CREDENTIAL_FILE",
    "SAGEIN_COMMUNITY_CONFIG_DIR", "SAGEIN_COMMUNITY_AGENT_ID",
)


def sub_of(token: str) -> str:
    return decode_jwt(token)[1]["sub"]


class TestBridgeJwtAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["ASYNX_CONFIG_PATH"] = str(self.tmp / "asynx" / "config.json")
        os.environ["ASYNX_LOG_PATH"] = str(self.tmp / "logs" / "failed-requests.jsonl")
        self.gw, self.gw_url, _ = run_fake_gateway()
        self.sagein, self.origin, _ = run_fake_sagein_server(self.gw_url)
        self.skill = self.tmp / "skill"
        self.context, self.cfg = install_fake_context(self.skill, self.origin)
        os.environ["PLAIERS_SKILL_CONTEXT"] = str(self.context)

    def tearDown(self) -> None:
        for s in (self.gw, self.sagein):
            s.shutdown()
            s.server_close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge_calls(self, cfg: Path | None = None) -> list[dict]:
        log = (cfg or self.cfg) / "bridge-calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def jwt_requests(self) -> int:
        return sum(1 for m, p, _ in self.sagein.requests if (m, p) == ("POST", "/api/agent/jwt"))

    def rewrite_context(self, **changes) -> None:
        data = json.loads(self.context.read_text())
        data.update(changes)
        self.context.write_text(json.dumps(data))

    # --- basic contract ---------------------------------------------------------------------------

    def test_jwt_from_recorded_argv_and_nothing_cached_by_skill(self) -> None:
        token, base_url = load_credentials()
        self.assertEqual(base_url, self.gw_url)
        self.assertEqual(token, self.sagein.issued[0])
        recorded = json.loads(self.context.read_text())["jwt_command"]
        self.assertEqual(self.bridge_calls()[0]["argv"], recorded[1:])
        # The skill keeps no JWT of its own: a new process (new session) asks the bridge again.
        load_credentials()
        self.assertEqual(len(self.bridge_calls()), 2)
        config_file = Path(os.environ["ASYNX_CONFIG_PATH"])
        self.assertFalse(config_file.exists() and "token" in json.loads(config_file.read_text()))

    def test_session_reuses_token_in_memory_only(self) -> None:
        session = load_auth_session()
        self.assertEqual(session.ensure_valid_token(), session.ensure_valid_token())
        self.assertEqual(len(self.bridge_calls()), 1)

    def test_no_default_origin_and_env_origin_ignored(self) -> None:
        self.assertFalse(hasattr(constants, "DEFAULT_ORIGIN"))
        os.environ["SAGEIN_COMMUNITY_ORIGIN"] = "https://plaiers.ai"
        session = load_auth_session()
        self.assertEqual(session.origin, self.origin)

    def test_missing_context_needs_login_with_install_hint(self) -> None:
        self.context.unlink()
        with self.assertRaises(AsxError) as ctx:
            load_auth_session()
        self.assertEqual(ctx.exception.code, "need_login")
        self.assertEqual(ctx.exception.details["reason_code"], "missing_context")
        self.assertIn("get_skill", ctx.exception.message)
        self.assertEqual(self.bridge_calls(), [])
        status = config_status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["credential_source"], "none")
        self.assertFalse(doctor()["ok"])

    def test_missing_context_ignores_legacy_credentials_and_bindings(self) -> None:
        legacy = self.tmp / "legacy"
        (legacy / "credentials").mkdir(parents=True)
        (legacy / "credentials" / "0123456789abcdef.json").write_text(json.dumps({"access_token": PAT}))
        (legacy / "bindings").mkdir()
        (legacy / "bindings" / "x.json").write_text(json.dumps({"origin": self.origin, "credential_file": "x"}))
        os.environ["SAGEIN_COMMUNITY_CONFIG_DIR"] = str(legacy)
        os.environ["SAGEIN_COMMUNITY_CREDENTIAL_FILE"] = str(legacy / "credentials" / "0123456789abcdef.json")
        self.context.unlink()
        with self.assertRaises(AsxError) as ctx:
            load_auth_session()
        self.assertEqual(ctx.exception.details["reason_code"], "missing_context")
        self.assertEqual(self.sagein.requests, [])

    def test_invalid_contexts_are_refused(self) -> None:
        argv = json.loads(self.context.read_text())["jwt_command"]
        cases = {
            "pat": {"jwt_command": argv, "token": PAT},
            "flag": {"jwt_command": argv + ["--import-env", "1"]},
            "origin": {"jwt_command": argv, "origin": "https://plaiers.ai"},
            "schema": {"schema_version": 2},
            "argv": {"jwt_command": "node get-skill-jwt.mjs"},
        }
        original = self.context.read_text()
        for name, change in cases.items():
            with self.subTest(name):
                self.context.write_text(original)
                self.rewrite_context(**change)
                with self.assertRaises(AsxError) as ctx:
                    load_auth_session()
                self.assertEqual(ctx.exception.code, "need_login")
                self.assertEqual(ctx.exception.details["reason_code"], "invalid_context")
                self.assertNotIn(PAT, json.dumps(ctx.exception.payload()))
        self.assertEqual(self.bridge_calls(), [])

    def test_missing_scope(self) -> None:
        self.sagein.scope_ok = False
        with self.assertRaises(AsxError) as ctx:
            load_auth_session()
        self.assertEqual(ctx.exception.code, "scope_missing")

    def test_status_and_client(self) -> None:
        status = config_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["credential_source"], "credential_context")
        self.assertEqual(status["origin"], self.origin)
        self.assertNotIn(PAT, json.dumps(status))
        self.assertEqual(load_client().base_url, self.gw_url)

    def test_direct_env_credentials_are_refused_with_migration_hint(self) -> None:
        # PR #10 Q6: no ASYNX_PARTNER_TOKEN / ASYNX_BASE_URL bypass, even next to a valid context.
        for name, value in (("ASYNX_PARTNER_TOKEN", "pt_env"), ("ASYNX_BASE_URL", "http://127.0.0.1:1/api")):
            os.environ[name] = value
            try:
                with self.assertRaises(AsxError) as ctx:
                    load_auth_session()
                self.assertEqual(ctx.exception.code, "legacy_credentials_removed")
                self.assertEqual(ctx.exception.details["variables"], [name])
                self.assertIn("get-skill-jwt.mjs", str(ctx.exception))
                status = config_status()
                self.assertFalse(status["configured"])
                self.assertEqual(status["legacy_credential_env"], [name])
            finally:
                del os.environ[name]
        self.assertEqual(self.bridge_calls(), [])
        self.assertTrue(config_status()["configured"])

    def test_missing_gateway_in_asynx_config_has_no_default(self) -> None:
        del self.sagein.asynx_config["baseUrl"]
        with self.assertRaises(AsxError) as ctx:
            load_client()
        self.assertEqual(ctx.exception.code, "asynx_config_invalid")

    def test_bridge_jwt_with_wrong_kid_is_rejected_without_fallback(self) -> None:
        self.sagein.asynx_config["kid"] = "k1"
        bridge = self.tmp / "bad-kid-bridge.py"
        bridge.write_text(f"print({make_test_jwt(kid='old')!r})\n")
        self.rewrite_context(jwt_command=[sys.executable, str(bridge), "--agent-id", "agent-a", "--origin", self.origin])
        with self.assertRaises(AsxError) as ctx:
            load_auth_session()
        self.assertEqual(ctx.exception.code, "jwt_invalid")
        self.assertEqual(ctx.exception.details["reason_code"], "kid_mismatch")
        self.assertEqual(self.jwt_requests(), 0)

    # --- identity switching (PR #10 P1 scenarios) ---------------------------------------------------

    def test_account_switch_env_token_mode(self) -> None:
        shutil.rmtree(self.cfg / "bindings")
        self.sagein.pats.update({PAT: "user_a", PAT_B: "user_b"})
        os.environ["SAGEIN_COMMUNITY_TOKEN"] = PAT
        install_fake_context(self.skill, self.origin, config_dir=self.cfg, account_id="user_a", binding=False)
        self.assertEqual(sub_of(load_credentials()[0]), "user_a")
        os.environ["SAGEIN_COMMUNITY_TOKEN"] = PAT_B
        install_fake_context(self.skill, self.origin, config_dir=self.cfg, account_id="user_b", binding=False)
        self.assertEqual(sub_of(load_credentials()[0]), "user_b")
        # Env token of A with the context pinned to B: the bridge refuses; the skill does not fall back.
        os.environ["SAGEIN_COMMUNITY_TOKEN"] = PAT
        with self.assertRaises(AsxError) as ctx:
            load_credentials()
        self.assertEqual(ctx.exception.details["reason_code"], "bridge_failed")
        self.assertIn("does not match", ctx.exception.details["bridge_error"])

    def test_account_switch_binding_mode(self) -> None:
        self.sagein.pats[PAT_B] = "user_2"
        self.assertEqual(sub_of(load_credentials()[0]), "user_1")
        write_binding(self.cfg, "agent-a", "user_2", PAT_B)
        self.assertEqual(sub_of(load_credentials()[0]), "user_2")

    def test_pat_rotation(self) -> None:
        write_binding(self.cfg, "agent-a", "user_1", PAT, cache=True)
        first = load_credentials()[0]
        self.assertEqual(load_credentials()[0], first)  # bridge cache hit, same identity
        del self.sagein.pats[PAT]
        self.sagein.pats[PAT_C] = "user_1"
        write_binding(self.cfg, "agent-a", "user_1", PAT_C, cache=True)
        rotated = load_credentials()[0]
        self.assertNotEqual(rotated, first)
        self.assertEqual(self.jwt_requests(), 2)

    def test_pinned_account_mismatch(self) -> None:
        install_fake_context(self.skill, self.origin, config_dir=self.cfg, account_id="someone_else", binding=False)
        with self.assertRaises(AsxError) as ctx:
            load_credentials()
        self.assertEqual(ctx.exception.code, "need_login")
        self.assertIn("mismatch", ctx.exception.details["bridge_error"])
        self.assertEqual(self.jwt_requests(), 0)

    def test_spaces_in_paths_passed_unchanged(self) -> None:
        spaced = self.tmp / "My Bridge Dir"
        spaced.mkdir()
        bridge = spaced / "get skill jwt.py"
        shutil.copy(BRIDGE_SCRIPT, bridge)
        cfg = self.tmp / "Community Config"
        cred = self.tmp / "Cred Dir" / "cred file.json"
        context, _ = install_fake_context(
            self.tmp / "Skill Dir", self.origin, config_dir=cfg, credential_file=cred, account_id="user_1",
            bridge_script=bridge,
        )
        os.environ["PLAIERS_SKILL_CONTEXT"] = str(context)
        self.assertEqual(sub_of(load_credentials()[0]), "user_1")
        recorded = json.loads(context.read_text())["jwt_command"]
        self.assertEqual(self.bridge_calls(cfg)[0]["argv"], recorded[1:])
        self.assertIn("--credential-file", recorded)

    # --- gateway 401 ----------------------------------------------------------------------------------

    def test_gateway_401_reinvokes_bridge_once(self) -> None:
        client = load_client()
        self.gw.fail_next_auth_count = 1
        identity, _ = client.partner_identity()
        self.assertIn("budget", identity)
        self.assertEqual(len(self.bridge_calls()), 2)

    def test_gateway_401_same_token_is_not_retried(self) -> None:
        write_binding(self.cfg, "agent-a", "user_1", PAT, cache=True)
        client = load_client()
        self.gw.fail_next_auth_count = 5
        with self.assertRaises(AsxError) as ctx:
            client.partner_identity()
        self.assertEqual(ctx.exception.details["reason_code"], "gateway_rejected_jwt")
        self.assertEqual(len(self.bridge_calls()), 2)
        gateway_auth_attempts = [r for r in self.gw.received_requests if r[1].endswith("/partner/identity")]
        self.assertEqual(len(gateway_auth_attempts), 1)


class TestInstalledSkillCli(unittest.TestCase):
    """Default context location (<skill>/.plaiers-context.json) in an installed package with spaces in paths."""

    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill
        build_skill("audio")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.gw, self.gw_url, _ = run_fake_gateway()
        self.sagein, self.origin, _ = run_fake_sagein_server(self.gw_url)
        self.skill = self.tmp / "Agent Skills" / "plaiers-asynx-audio"
        shutil.copytree(REPO_ROOT / "skills" / "plaiers-asynx-audio", self.skill)
        self.env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
        self.env.update({
            "ASYNX_CONFIG_PATH": str(self.tmp / "config.json"),
            "ASYNX_STATE_PATH": str(self.tmp / "state.db"),
            "ASYNX_LOG_PATH": str(self.tmp / "logs" / "failed.jsonl"),
            "SAGEIN_COMMUNITY_ORIGIN": "https://plaiers.ai",
        })

    def tearDown(self) -> None:
        for s in (self.gw, self.sagein):
            s.shutdown()
            s.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.skill / "scripts" / "asynx.py"), *args],
                              capture_output=True, text=True, env=self.env, cwd=str(self.tmp))

    def test_whoami_via_installed_context_and_pat_never_printed(self) -> None:
        missing = self.run_cli("whoami")
        self.assertEqual(missing.returncode, 2, missing.stderr)
        error = json.loads(missing.stdout or missing.stderr)["error"]
        self.assertEqual(error["code"], "need_login")
        self.assertEqual(error["details"]["context_path"], str((self.skill / ".plaiers-context.json").resolve()))

        install_fake_context(self.skill, self.origin, config_dir=self.tmp / "Community Config")
        ok = self.run_cli("whoami")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["origin"], self.origin)

        self.gw.fail_next_auth_count = 5
        failed = self.run_cli("whoami")
        self.assertNotEqual(failed.returncode, 0)
        logs = "".join(p.read_text() for p in self.tmp.rglob("*.jsonl") if p.name != "bridge-calls.jsonl")
        for output in (missing.stdout, missing.stderr, ok.stdout, ok.stderr, failed.stdout, failed.stderr, logs):
            self.assertNotIn(PAT, output)
            self.assertNotIn("sagein_pat_", output)
        # Receipt hashes exclude the local context, so integrity checks ignore it.
        receipt = json.loads((self.skill / ".asx-install.json").read_text())
        self.assertNotIn(".plaiers-context.json", receipt["files"])


if __name__ == "__main__":
    unittest.main()
