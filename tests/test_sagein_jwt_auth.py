from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

from shared.asxlib.auth import credential_file_path, is_token_usable, load_auth_session, load_pat
from shared.asxlib.config import config_status, load_client, load_credentials
from shared.asxlib.constants import DEFAULT_ORIGIN
from shared.asxlib.errors import AsxError
from tests.fake_gateway import run_fake_gateway
from tests.fake_sagein_server import make_test_jwt, run_fake_sagein_server


class TestSageInJwtAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.cred = Path(self.tmp) / "cred.json"
        self._env = {k: os.environ.get(k) for k in (
            "ASYNX_CONFIG_PATH", "SAGEIN_COMMUNITY_ORIGIN", "SAGEIN_COMMUNITY_CREDENTIAL_FILE",
            "SAGEIN_COMMUNITY_CONFIG_DIR", "SAGEIN_COMMUNITY_AGENT_ID",
            "ASYNX_PARTNER_TOKEN", "ASYNX_BASE_URL")}
        os.environ.pop("ASYNX_PARTNER_TOKEN", None)
        os.environ.pop("ASYNX_BASE_URL", None)
        os.environ.pop("SAGEIN_COMMUNITY_CONFIG_DIR", None)
        os.environ.pop("SAGEIN_COMMUNITY_AGENT_ID", None)
        os.environ["ASYNX_CONFIG_PATH"] = str(Path(self.tmp) / "config.json")
        os.environ["SAGEIN_COMMUNITY_CREDENTIAL_FILE"] = str(self.cred)
        self.gw, self.gw_url, _ = run_fake_gateway()
        self.sagein, self.origin, _ = run_fake_sagein_server(self.gw_url, self.cred)
        os.environ["SAGEIN_COMMUNITY_ORIGIN"] = self.origin

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

    def test_exchange_pat_for_jwt_and_cache(self) -> None:
        token, base_url = load_credentials()
        self.assertEqual(base_url, self.gw_url)
        self.assertEqual(token, self.sagein.issued[0])
        # Second load reuses the cached JWT (no new exchange).
        token2, _ = load_credentials()
        self.assertEqual(token2, token)
        self.assertEqual(len(self.sagein.issued), 1)

    def test_refresh_when_near_expiry(self) -> None:
        cfg = self.sagein.asynx_config
        stale = make_test_jwt(exp=int(time.time()) + 10)
        self.assertFalse(is_token_usable(stale, cfg))
        from shared.asxlib.config import save_config
        save_config({"token": stale, "asynx": cfg})
        token, _ = load_credentials()
        self.assertNotEqual(token, stale)
        self.assertEqual(len(self.sagein.issued), 1)

    def test_refresh_on_kid_mismatch(self) -> None:
        from shared.asxlib.config import save_config
        save_config({"token": make_test_jwt(kid="old"), "asynx": self.sagein.asynx_config})
        token, _ = load_credentials()
        self.assertEqual(token, self.sagein.issued[0])

    def test_missing_pat_needs_login(self) -> None:
        self.cred.unlink()
        with self.assertRaises(AsxError) as ctx:
            load_auth_session()
        self.assertEqual(ctx.exception.code, "need_login")
        self.assertFalse(config_status()["has_pat"])

    def test_missing_scope_is_403(self) -> None:
        self.sagein.scope_ok = False
        with self.assertRaises(AsxError) as ctx:
            load_auth_session()
        self.assertEqual(ctx.exception.code, "scope_missing")

    def test_status_and_client(self) -> None:
        self.assertTrue(config_status()["has_pat"])
        client = load_client()
        self.assertEqual(client.base_url, self.gw_url)

    def test_env_partner_token_headless(self) -> None:
        os.environ["ASYNX_PARTNER_TOKEN"] = "pt_env"
        session = load_auth_session()
        self.assertEqual(session.token, "pt_env")
        self.assertEqual(self.sagein.issued, [])

    def test_default_origin_is_plaiers(self) -> None:
        self.assertEqual(DEFAULT_ORIGIN, "https://plaiers.ai")

    def test_bindings_discovery_single_and_agent_id(self) -> None:
        os.environ.pop("SAGEIN_COMMUNITY_CREDENTIAL_FILE", None)
        cfg_dir = Path(self.tmp) / "community-cfg"
        (cfg_dir / "bindings").mkdir(parents=True)
        (cfg_dir / "credentials").mkdir(parents=True)
        os.environ["SAGEIN_COMMUNITY_CONFIG_DIR"] = str(cfg_dir)

        pat_a = "sagein_pat_" + "a" * 64
        cred_a = cfg_dir / "credentials" / "cred-a.json"
        cred_a.write_text(json.dumps({"access_token": pat_a}), encoding="utf-8")
        hash_a = hashlib.sha256(
            json.dumps([self.origin, "agent-a"], separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        (cfg_dir / "bindings" / f"{hash_a}.json").write_text(
            json.dumps({
                "schema_version": 2,
                "origin": self.origin,
                "agent_id": "agent-a",
                "account_id": "u1",
                "credential_file": str(cred_a),
            }),
            encoding="utf-8",
        )

        # Unique binding for origin is discovered automatically without SAGEIN_COMMUNITY_CREDENTIAL_FILE
        self.assertEqual(credential_file_path(self.origin), cred_a)
        self.assertEqual(load_pat(self.origin), pat_a)

        # Adding a second agent binding makes auto-discovery ambiguous until SAGEIN_COMMUNITY_AGENT_ID is set
        pat_b = "sagein_pat_" + "b" * 64
        cred_b = cfg_dir / "credentials" / "cred-b.json"
        cred_b.write_text(json.dumps({"access_token": pat_b}), encoding="utf-8")
        hash_b = hashlib.sha256(
            json.dumps([self.origin, "agent-b"], separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        (cfg_dir / "bindings" / f"{hash_b}.json").write_text(
            json.dumps({
                "schema_version": 2,
                "origin": self.origin,
                "agent_id": "agent-b",
                "account_id": "u2",
                "credential_file": str(cred_b),
            }),
            encoding="utf-8",
        )

        with self.assertRaises(AsxError) as ctx:
            load_pat(self.origin)
        self.assertEqual(ctx.exception.details.get("reason_code"), "ambiguous_credentials")

        os.environ["SAGEIN_COMMUNITY_AGENT_ID"] = "agent-b"
        self.assertEqual(credential_file_path(self.origin), cred_b)
        self.assertEqual(load_pat(self.origin), pat_b)


if __name__ == "__main__":
    unittest.main()
