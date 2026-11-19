from __future__ import annotations

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

from shared.asxlib.auth import is_token_usable, load_auth_session
from shared.asxlib.config import config_status, load_client, load_credentials
from shared.asxlib.errors import AsxError
from tests.fake_gateway import run_fake_gateway
from tests.fake_sagein_server import make_test_jwt, run_fake_sagein_server


class TestSageInJwtAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.cred = Path(self.tmp) / "cred.json"
        self._env = {k: os.environ.get(k) for k in (
            "ASYNX_CONFIG_PATH", "SAGEIN_COMMUNITY_ORIGIN", "SAGEIN_COMMUNITY_CREDENTIAL_FILE",
            "ASYNX_PARTNER_TOKEN", "ASYNX_BASE_URL")}
        os.environ.pop("ASYNX_PARTNER_TOKEN", None)
        os.environ.pop("ASYNX_BASE_URL", None)
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


if __name__ == "__main__":
    unittest.main()
