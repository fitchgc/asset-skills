from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

from shared.asxlib.auth import (
    LoginSvrClient,
    load_auth_session,
    login_and_provision,
    register_login_ui_handler,
    save_auth_session,
)
from shared.asxlib.client import AsynxClient
from shared.asxlib.config import config_status, doctor, load_client, load_credentials
from shared.asxlib.constants import TERMINAL_STATUSES
from shared.asxlib.errors import AsxError
from shared.asxlib.tasks import run_task_submission
from tests.fake_gateway import run_fake_gateway, run_fake_storage
from tests.fake_login_server import make_test_jwt, run_fake_login_server


class TestIdentityServiceClient(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["ASYNX_CONFIG_PATH"] = str(Path(self.temp_dir) / "config.json")
        self.login_server, self.login_url, _ = run_fake_login_server("http://127.0.0.1:18080")
        self.client = LoginSvrClient(self.login_url)

    def tearDown(self) -> None:
        self.login_server.shutdown()
        self.login_server.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_CONFIG_PATH", None)

    def test_asynx_config_register_login_profile_refresh_change_password(self) -> None:
        cfg = self.client.asynx_config()
        self.assertEqual(cfg["baseUrl"], "http://127.0.0.1:18080")
        self.assertEqual(cfg["tokenHeader"], "X-Asynx-Partner-Token")
        self.assertEqual(cfg["kid"], "k1")

        avail = self.client.check_username("alice_01")
        self.assertTrue(avail["available"])
        self.assertFalse(avail["exists"])

        reg = self.client.register("alice_01", "secret_123", "Alice")
        self.assertIn("token", reg)
        self.assertIn("refreshToken", reg)
        self.assertEqual(reg["user"]["username"], "alice_01")

        avail_after = self.client.check_username("alice_01")
        self.assertFalse(avail_after["available"])
        self.assertTrue(avail_after["exists"])

        prof = self.client.profile(reg["token"])
        self.assertEqual(prof["username"], "alice_01")

        refreshed = self.client.refresh(reg["refreshToken"])
        self.assertNotEqual(refreshed["refreshToken"], reg["refreshToken"])

        # Reusing rotated refreshToken fails with code 401
        with self.assertRaises(AsxError) as ctx:
            self.client.refresh(reg["refreshToken"])
        self.assertEqual(ctx.exception.details.get("login_svr_code"), 401)

        changed = self.client.change_password(refreshed["token"], "secret_123", "new_secret_456")
        self.assertNotEqual(changed["token"], refreshed["token"])
        self.assertNotEqual(changed["refreshToken"], refreshed["refreshToken"])

        # Login with new password succeeds
        logged_in = self.client.login("alice_01", "new_secret_456")
        self.assertEqual(logged_in["user"]["username"], "alice_01")

    def test_error_code_mapping(self) -> None:
        self.login_server.seed_user("bob_01", "pass_123", locked=False)
        self.login_server.seed_user("locked_u", "pass_123", locked=True)
        self.login_server.seed_user("deleted_u", "pass_123", deleted=True)

        # 1001 invalid username
        with self.assertRaises(AsxError) as c1001:
            self.client.check_username("AB")
        self.assertEqual(c1001.exception.details.get("login_svr_code"), 1001)

        # 1002 duplicate username
        with self.assertRaises(AsxError) as c1002:
            self.client.register("bob_01", "pass_123")
        self.assertEqual(c1002.exception.details.get("login_svr_code"), 1002)

        # 1003 wrong password
        with self.assertRaises(AsxError) as c1003:
            self.client.login("bob_01", "wrong_pass")
        self.assertEqual(c1003.exception.details.get("login_svr_code"), 1003)

        # 12 locked
        with self.assertRaises(AsxError) as c12:
            self.client.login("locked_u", "pass_123")
        self.assertEqual(c12.exception.details.get("login_svr_code"), 12)
        self.assertEqual(c12.exception.code, "account_locked")

        # 15 deleted
        with self.assertRaises(AsxError) as c15:
            self.client.login("deleted_u", "pass_123")
        self.assertEqual(c15.exception.details.get("login_svr_code"), 15)
        self.assertEqual(c15.exception.code, "account_deleted")

        # 10 missing token on profile / refresh
        with self.assertRaises(AsxError) as c10:
            self.client.refresh("")
        self.assertEqual(c10.exception.details.get("login_svr_code"), 10)

        # 11 missing user on refresh
        self.login_server.forced_refresh_error = (11, "need login")
        with self.assertRaises(AsxError) as c11:
            self.client.refresh("rt_missing")
        self.assertEqual(c11.exception.details.get("login_svr_code"), 11)
        self.login_server.forced_refresh_error = None

        # 1004 invalid old password
        login_res = self.client.login("bob_01", "pass_123")
        with self.assertRaises(AsxError) as c1004:
            self.client.change_password(login_res["token"], "wrong_old", "new_pass_456")
        self.assertEqual(c1004.exception.details.get("login_svr_code"), 1004)


class TestAssetGatewayProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["ASYNX_CONFIG_PATH"] = str(Path(self.temp_dir) / "config.json")
        os.environ["ASYNX_CACHE_PATH"] = str(Path(self.temp_dir) / "models.json")
        self.gateway_server, self.gateway_url, _ = run_fake_gateway()
        self.storage_server, self.storage_url, _ = run_fake_storage()
        self.client = AsynxClient(self.gateway_url, "pt_valid_token_1")

    def tearDown(self) -> None:
        self.gateway_server.shutdown()
        self.gateway_server.server_close()
        self.storage_server.shutdown()
        self.storage_server.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ASYNX_CONFIG_PATH", None)
        os.environ.pop("ASYNX_CACHE_PATH", None)

    def test_exclusive_partner_token_and_new_gateway_endpoints(self) -> None:
        identity, req_id = self.client.partner_identity()
        self.assertEqual(identity["api_key"]["status"], "active")
        self.assertEqual(req_id, "req-identity-1")

        guide, g_req = self.client.model_guide("gpt-image-2.5-sunburst")
        self.assertEqual(guide["model"], "gpt-image-2.5-sunburst")
        self.assertTrue(g_req)

        quote, q_req = self.client.quote("image.generate", {"model": "gpt-image-2.5-sunburst", "input": {"prompt": "cat"}})
        self.assertEqual(quote["currency"], "USD")
        self.assertTrue(q_req)

        sample_file = Path(self.temp_dir) / "ref.png"
        sample_file.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
        uploaded, f_req = self.client.upload_file(sample_file)
        self.assertTrue(uploaded["id"].startswith("file_"))
        self.assertTrue(f_req)

        # Verify no Authorization header was ever sent to gateway
        self.assertGreater(len(self.gateway_server.received_requests), 0)
        for _method, _path, hdrs in self.gateway_server.received_requests:
            lowered = {k.lower(): v for k, v in hdrs.items()}
            self.assertNotIn("authorization", lowered)
            self.assertEqual(lowered.get("x-asynx-partner-token"), "pt_valid_token_1")

    def test_cross_origin_redirect_strips_partner_token(self) -> None:
        self.storage_server.objects["/bucket/out.png"] = b"STORAGE_PNG_BYTES"
        self.storage_server.object_mimes["/bucket/out.png"] = "image/png"
        self.gateway_server.redirect_urls["task_redir:0"] = f"{self.storage_url}/bucket/out.png"

        dest = Path(self.temp_dir) / "downloaded.png"
        info = self.client.download_asset_to_file("task_redir", 0, dest)
        self.assertEqual(dest.read_bytes(), b"STORAGE_PNG_BYTES")
        self.assertEqual(info["bytes"], len(b"STORAGE_PNG_BYTES"))

        # Gateway received X-Asynx-Partner-Token
        gw_headers = {k.lower(): v for k, v in self.gateway_server.received_requests[-1][2].items()}
        self.assertEqual(gw_headers.get("x-asynx-partner-token"), "pt_valid_token_1")

        # External storage origin must NOT receive X-Asynx-Partner-Token or Authorization
        st_headers = {k.lower(): v for k, v in self.storage_server.received_headers[-1].items()}
        self.assertNotIn("x-asynx-partner-token", st_headers)
        self.assertNotIn("authorization", st_headers)

    def test_relative_download_url_resolved_against_base_url(self) -> None:
        self.gateway_server.asset_data["task_rel:0"] = b"RELATIVE_URL_PNG"
        self.gateway_server.asset_mimes["task_rel:0"] = "image/png"

        dest1 = Path(self.temp_dir) / "rel_leading_slash.png"
        info1 = self.client.download_asset_to_file(
            "task_rel", 0, dest1, download_url="/v1/tasks/task_rel/assets/0"
        )
        self.assertEqual(dest1.read_bytes(), b"RELATIVE_URL_PNG")
        self.assertEqual(info1["bytes"], len(b"RELATIVE_URL_PNG"))

        dest2 = Path(self.temp_dir) / "rel_no_slash.png"
        info2 = self.client.download_asset_to_file(
            "task_rel", 0, dest2, download_url="v1/tasks/task_rel/assets/0"
        )
        self.assertEqual(dest2.read_bytes(), b"RELATIVE_URL_PNG")
        self.assertEqual(info2["bytes"], len(b"RELATIVE_URL_PNG"))

    def test_blocked_is_terminal_status_and_error_captures_http_status_and_request_id(self) -> None:
        self.assertIn("blocked", TERMINAL_STATUSES)
        self.gateway_server.expired_tokens.add("pt_bad")
        bad_client = AsynxClient(self.gateway_url, "pt_bad")
        with self.assertRaises(AsxError) as ctx:
            bad_client.partner_identity()
        self.assertEqual(ctx.exception.code, "invalid_partner_token")
        self.assertEqual(ctx.exception.http_status, 401)
        self.assertEqual(ctx.exception.request_id, "req-err-expired")


class TestTwoTierRefreshAndProvisioning(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.json"
        self.state_db = Path(self.temp_dir) / "state.db"
        self.cache_file = Path(self.temp_dir) / "models.json"
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CACHE_PATH"] = str(self.cache_file)
        os.environ["ASYNX_INITIAL_POLL_DELAY_SECONDS"] = "0.01"

        self.gateway_server, self.gateway_url, _ = run_fake_gateway()
        self.login_server, self.login_url, _ = run_fake_login_server(self.gateway_url)
        os.environ["ASYNX_LOGIN_SVR_URL"] = self.login_url

    def tearDown(self) -> None:
        self.login_server.shutdown()
        self.login_server.server_close()
        self.gateway_server.shutdown()
        self.gateway_server.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        for var in (
            "ASYNX_CONFIG_PATH",
            "ASYNX_STATE_PATH",
            "ASYNX_CACHE_PATH",
            "ASYNX_LOGIN_SVR_URL",
            "ASYNX_PARTNER_TOKEN",
            "ASYNX_BASE_URL",
        ):
            os.environ.pop(var, None)

    def test_missing_and_unrefreshable_credentials_raise_need_login(self) -> None:
        with self.assertRaises(AsxError) as ctx:
            load_credentials()
        self.assertEqual(ctx.exception.code, "need_login")

        # Expired token + invalid refreshToken -> clears tokens & raises need_login
        bad_jwt = make_test_jwt(exp=int(time.time()) - 10)
        save_auth_session(
            {
                "token": bad_jwt,
                "refreshToken": "rt_invalid",
                "asynx": dict(self.login_server.asynx_config),
                "user": {"id": "u1", "username": "alice_01"},
            },
            login_svr_url=self.login_url,
        )
        with self.assertRaises(AsxError) as ctx2:
            load_credentials()
        self.assertEqual(ctx2.exception.code, "need_login")
        saved = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertNotIn("token", saved)
        self.assertNotIn("refreshToken", saved)

    def test_headless_partner_token_and_base_url_override(self) -> None:
        os.environ["ASYNX_PARTNER_TOKEN"] = "pt_headless_ci_1"
        os.environ["ASYNX_BASE_URL"] = self.gateway_url
        token, base_url = load_credentials()
        self.assertEqual(token, "pt_headless_ci_1")
        self.assertEqual(base_url, self.gateway_url)
        status = config_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["credential_source"], "environment")
        doc = doctor(verify=True, modality="image")
        self.assertTrue(doc["ok"])
        self.assertTrue(doc["verification"]["ok"])

    def test_headless_partner_token_without_base_url_falls_back_to_default(self) -> None:
        from shared.asxlib.constants import DEFAULT_BASE_URL

        os.environ["ASYNX_PARTNER_TOKEN"] = "pt_headless_no_url"
        os.environ.pop("ASYNX_BASE_URL", None)
        status = config_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["credential_source"], "environment")
        self.assertEqual(status["base_url"], DEFAULT_BASE_URL)
        self.assertTrue(status["base_url_valid"])

        doc = doctor(verify=False, modality="image")
        self.assertTrue(doc["ok"], doc)
        self.assertFalse(any(i.get("code") == "invalid_base_url" for i in doc["issues"]))

    def test_non_json_401_on_refresh_clears_credentials_and_releases_lock_before_login_ui(self) -> None:
        self.login_server.seed_user("alice_01", "secret_123", "Alice")
        bad_jwt = make_test_jwt(exp=int(time.time()) - 10)
        save_auth_session(
            {
                "token": bad_jwt,
                "refreshToken": "rt_triggers_non_json_401",
                "asynx": dict(self.login_server.asynx_config),
                "user": {"id": "u1", "username": "alice_01"},
            },
            login_svr_url=self.login_url,
        )

        orig_do_post = self.login_server.RequestHandlerClass.do_POST

        def _non_json_401_refresh(handler: Any) -> None:
            if handler.path == "/api/user/refresh":
                body = b"Unauthorized"
                handler.send_response(401)
                handler.send_header("Content-Type", "text/plain")
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
                return
            orig_do_post(handler)

        # 1. Without login UI handler: clears session & raises need_login
        with unittest.mock.patch.object(self.login_server.RequestHandlerClass, "do_POST", _non_json_401_refresh):
            with self.assertRaises(AsxError) as ctx:
                load_auth_session(allow_login_ui=False)
        self.assertEqual(ctx.exception.code, "need_login")
        saved = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertNotIn("token", saved)
        self.assertNotIn("refreshToken", saved)

        # 2. With login UI handler: releases AuthSession._lock before calling _login_ui_handler
        save_auth_session(
            {
                "token": bad_jwt,
                "refreshToken": "rt_triggers_non_json_401",
                "asynx": dict(self.login_server.asynx_config),
                "user": {"id": "u1", "username": "alice_01"},
            },
            login_svr_url=self.login_url,
        )
        lock_free_inside_handler: list[bool] = []
        session_holder: list[Any] = []

        from shared.asxlib.auth import AuthSession

        orig_trigger = AuthSession._trigger_login_or_raise

        def _capture_session(self_sess: Any, *args: Any, **kwargs: Any) -> str:
            session_holder.append(self_sess)
            return orig_trigger(self_sess, *args, **kwargs)

        def _fake_login_ui(login_svr_url: str) -> dict[str, Any]:
            if session_holder:
                acquired = session_holder[0]._lock.acquire(blocking=False)
                lock_free_inside_handler.append(acquired)
                if acquired:
                    session_holder[0]._lock.release()
            res = LoginSvrClient(login_svr_url).login("alice_01", "secret_123")
            save_auth_session(res, login_svr_url=login_svr_url)
            return res

        register_login_ui_handler(_fake_login_ui)
        self.addCleanup(register_login_ui_handler, None)
        with (
            unittest.mock.patch.object(self.login_server.RequestHandlerClass, "do_POST", _non_json_401_refresh),
            unittest.mock.patch.object(AuthSession, "_trigger_login_or_raise", _capture_session),
        ):
            recovered = load_auth_session(allow_login_ui=True)
        self.assertTrue(recovered.token)
        self.assertEqual(lock_free_inside_handler, [True])

    def test_models_guide_and_quote_dry_run_and_offline_ui_commands(self) -> None:
        from src.build_skills import build_skill

        for mod in ("image", "audio", "video", "3d"):
            build_skill(mod)

        self.login_server.seed_user("cli_guide", "secret_123", "Guide")
        login_and_provision("cli_guide", "secret_123", login_svr_url=self.login_url)

        env = {
            **os.environ,
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CACHE_PATH": str(self.cache_file),
            "ASYNX_LOGIN_SVR_URL": self.login_url,
            "ASYNX_NO_OPEN_BROWSER": "1",
        }

        # 1. models --guide across skill CLI
        img_py = REPO_ROOT / "skills" / "asynx-image-pro" / "scripts" / "asynx.py"
        g_proc = subprocess.run(
            [sys.executable, str(img_py), "models", "--guide", "gpt-image-2.5-sunburst"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(g_proc.returncode, 0, g_proc.stderr + g_proc.stdout)
        guide_payload = json.loads(g_proc.stdout)
        self.assertTrue(guide_payload["ok"])
        self.assertEqual(guide_payload["guide"]["model"], "gpt-image-2.5-sunburst")

        # 2. --quote dry-run does NOT submit /v1/tasks across all 4 generation skills
        before_subs = len(self.gateway_server.submissions)
        for skill_name, argv in (
            ("asynx-image-pro", ["generate", "--prompt", "a dragon", "--quote"]),
            ("asynx-audio", ["sfx", "--prompt", "sword clash", "--quote"]),
            ("asynx-video", ["generate", "--prompt", "waves crashing", "--quote"]),
            ("asynx-3d", ["generate", "--prompt", "lowpoly chest", "--quote"]),
        ):
            cli_py = REPO_ROOT / "skills" / skill_name / "scripts" / "asynx.py"
            q_proc = subprocess.run(
                [sys.executable, str(cli_py), *argv],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(q_proc.returncode, 0, f"{skill_name}: {q_proc.stderr}{q_proc.stdout}")
            q_payload = json.loads(q_proc.stdout)
            self.assertTrue(q_payload["ok"])
            self.assertTrue(q_payload["quoted"])
            self.assertEqual(q_payload["quote"]["currency"], "USD")
        self.assertEqual(len(self.gateway_server.submissions), before_subs)

        # 3. Offline UI commands in video & model3d pass client=None without requiring credentials
        offline_env = {**env}
        offline_env.pop("ASYNX_PARTNER_TOKEN", None)
        self.config_file.unlink(missing_ok=True)
        for skill_name, submod, argv in (
            ("asynx-video", "video", ["ui", "frame-sequence", "--no-open"]),
            ("asynx-video", "video", ["ui", "preview-sequence", "--atlas", "a.png", "--manifest", "m.json", "--no-open"]),
            ("asynx-3d", "3d", ["ui", "viewer", "--no-open"]),
            ("asynx-3d", "3d", ["ui", "render-sequence", "--no-open"]),
        ):
            scripts_dir = str(REPO_ROOT / "skills" / skill_name / "scripts")
            sys.path.insert(0, scripts_dir)
            try:
                import asxlib.cli as built_cli
                import importlib
                built_cli = importlib.reload(built_cli)
                seen_clients: list[Any] = []
                with unittest.mock.patch(
                    "asxlib.ui_server.launch_workbench",
                    side_effect=lambda **kw: (seen_clients.append(kw.get("client")) or ({"ok": True}, 0)),
                ):
                    parsed = built_cli.parser().parse_args([*argv, "--no-login-ui"])
                    payload, code = built_cli.execute(parsed)
                    self.assertEqual(code, 0, f"{skill_name} {submod}: {payload}")
                    self.assertEqual(seen_clients, [None])
            finally:
                if scripts_dir in sys.path:
                    sys.path.remove(scripts_dir)
                for mod_name in [m for m in list(sys.modules) if m == "asxlib" or m.startswith("asxlib.")]:
                    sys.modules.pop(mod_name, None)

    def test_proactive_refresh_on_near_expiry_and_config_mismatch(self) -> None:
        self.login_server.seed_user("alice_01", "secret_123", "Alice")
        session, identity = login_and_provision("alice_01", "secret_123", login_svr_url=self.login_url)
        self.assertEqual(identity["api_key"]["status"], "active")
        # Verify partner_identity was called upon login
        self.assertTrue(any(p == "/v1/partner/identity" for _, p, _ in self.gateway_server.received_requests))

        # 1. Near expiry (exp - now <= 60) triggers proactive refresh and persists rotated tokens
        near_exp_jwt = make_test_jwt(exp=int(time.time()) + 30)
        save_auth_session(
            {
                "token": near_exp_jwt,
                "refreshToken": session.refresh_token,
                "asynx": dict(self.login_server.asynx_config),
                "user": session.user,
            },
            login_svr_url=self.login_url,
        )
        new_tok, new_url = load_credentials()
        self.assertNotEqual(new_tok, near_exp_jwt)
        self.assertEqual(new_url, self.gateway_url)
        persisted = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["token"], new_tok)
        self.assertNotEqual(persisted["refreshToken"], session.refresh_token)

        # 2. kid mismatch against /api/user/asynx-config triggers proactive refresh
        self.login_server.asynx_config["kid"] = "k2"
        tok_after_kid, _ = load_credentials()
        self.assertNotEqual(tok_after_kid, new_tok)
        persisted_after = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted_after["asynx"]["kid"], "k2")

        # 3. iss mismatch triggers proactive refresh
        self.login_server.asynx_config["issuer"] = "login_svr_v2"
        tok_after_iss, _ = load_credentials()
        self.assertNotEqual(tok_after_iss, tok_after_kid)

        # 4. aud mismatch triggers proactive refresh
        self.login_server.asynx_config["audience"] = "asynx_v2"
        tok_after_aud, _ = load_credentials()
        self.assertNotEqual(tok_after_aud, tok_after_iss)

    def test_reactive_refresh_during_long_task_poll_and_download(self) -> None:
        self.login_server.seed_user("alice_01", "secret_123", "Alice")
        session, _ = login_and_provision("alice_01", "secret_123", login_svr_url=self.login_url)
        old_token = session.token
        old_refresh = session.refresh_token

        # Simulate token accepted at submit, then rejected with 401 during poll
        client = load_client()
        self.gateway_server.default_submit_status = "queued"
        self.gateway_server.next_task_poll_sequence = ["running", "succeeded"]
        self.gateway_server.asset_data["task_1:0"] = b"PNG_DATA"

        # Expire the old token right after partner_identity / submit by marking it expired after 1st poll:
        # Simpler deterministic way: mark old_token expired right now, so partner_identity itself hits 401?
        # Even better: test poll hitting 401 by setting fail_next_auth_count or expiring old_token on poll!
        orig_do_get = self.gateway_server.RequestHandlerClass.do_GET

        def _expire_on_task_poll(handler: Any) -> None:
            if handler.path.startswith("/v1/tasks/task_"):
                self.gateway_server.expired_tokens.add(old_token)
            orig_do_get(handler)

        with unittest.mock.patch.object(self.gateway_server.RequestHandlerClass, "do_GET", _expire_on_task_poll):
            out_dir = Path(self.temp_dir) / "out"
            payload, code = run_task_submission(
                client,
                task_type="image.generate",
                body={"model": "gpt-image-2.5-sunburst", "input": {"prompt": "test"}},
                model="gpt-image-2.5-sunburst",
                output_dir=str(out_dir),
                idempotency_key="idem-reactive-1",
                modality="image",
            )

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], "task_1")
        self.assertEqual(Path(payload["files"][0]).read_bytes(), b"PNG_DATA")
        # Rotated token & refreshToken persisted atomically
        persisted = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertNotEqual(persisted["token"], old_token)
        self.assertNotEqual(persisted["refreshToken"], old_refresh)

    def test_quota_exhausted_prevents_task_submission_and_blocked_status(self) -> None:
        self.login_server.seed_user("alice_01", "secret_123", "Alice")
        login_and_provision("alice_01", "secret_123", login_svr_url=self.login_url)
        client = load_client()

        # 1. Quota exhausted blocks submission before POST /v1/tasks
        self.gateway_server.partner_identity_response = {
            "api_key": {"status": "quota_exhausted", "concurrency_limit": 5},
            "usage": {"active_tasks": 0},
            "budget": {"remaining_amount": "0.00000000"},
        }
        with self.assertRaises(AsxError) as q_ctx:
            run_task_submission(
                client,
                task_type="image.generate",
                body={"model": "gpt-image-2.5-sunburst", "input": {"prompt": "cat"}},
                model="gpt-image-2.5-sunburst",
                output_dir=str(Path(self.temp_dir) / "out"),
                idempotency_key="idem-quota-1",
                modality="image",
            )
        self.assertEqual(q_ctx.exception.code, "quota_exhausted")
        self.assertEqual(len(self.gateway_server.submissions), 0)

        # 2. Restored quota allows submission; terminal status 'blocked' surfaces error cleanly
        self.gateway_server.partner_identity_response["api_key"]["status"] = "active"
        self.gateway_server.default_submit_status = "queued"
        self.gateway_server.next_task_poll_sequence = ["blocked"]
        payload, exit_code = run_task_submission(
            client,
            task_type="image.generate",
            body={"model": "gpt-image-2.5-sunburst", "input": {"prompt": "blocked"}},
            model="gpt-image-2.5-sunburst",
            output_dir=str(Path(self.temp_dir) / "out"),
            idempotency_key="idem-blocked-1",
            modality="image",
        )
        self.assertEqual(exit_code, 4)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["error"]["code"], "safety_blocked")

    def test_reactive_refresh_during_asset_download(self) -> None:
        self.login_server.seed_user("alice_01", "secret_123", "Alice")
        session, _ = login_and_provision("alice_01", "secret_123", login_svr_url=self.login_url)
        old_token = session.token
        self.gateway_server.asset_data["task_dl_401:0"] = b"ASSET_AFTER_REFRESH"
        self.gateway_server.expired_tokens.add(old_token)

        client = load_client()
        # Because load_client() checks pre-flight, token is still locally valid until gateway rejects it.
        dest = Path(self.temp_dir) / "dl.png"
        res = client.download_asset_to_file("task_dl_401", 0, dest)
        self.assertEqual(dest.read_bytes(), b"ASSET_AFTER_REFRESH")
        self.assertEqual(res["bytes"], len(b"ASSET_AFTER_REFRESH"))
        persisted = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertNotEqual(persisted["token"], old_token)

    def test_login_ui_handler_seam_and_batch_submit(self) -> None:
        self.login_server.seed_user("seam_user", "secret_123", "Seam")
        called: list[str] = []

        def _fake_login_ui(login_svr_url: str) -> dict[str, Any]:
            called.append(login_svr_url)
            login_res = LoginSvrClient(login_svr_url).login("seam_user", "secret_123")
            save_auth_session(login_res, login_svr_url=login_svr_url)
            return login_res

        register_login_ui_handler(_fake_login_ui)
        self.addCleanup(register_login_ui_handler, None)

        client = load_client()
        self.assertEqual(called, [self.login_url])
        identity, _ = client.partner_identity()
        self.assertEqual(identity["api_key"]["status"], "active")

        # Verify batch create + poll via built asynx-image-pro CLI uses updated submit(task_type, body, idempotency_key)
        from src.build_skills import build_skill
        build_skill("image")
        asynx_py = REPO_ROOT / "skills" / "asynx-image-pro" / "scripts" / "asynx.py"
        batch_out = Path(self.temp_dir) / "batch_out"
        self.gateway_server.asset_data["task_1:0"] = b"\x89PNG\r\n\x1a\nBATCH_PNG"
        env = {
            **os.environ,
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CACHE_PATH": str(self.cache_file),
            "ASYNX_LOGIN_SVR_URL": self.login_url,
        }
        p1 = subprocess.run(
            [
                sys.executable,
                str(asynx_py),
                "batch",
                "create",
                "--prompt",
                "batch cat",
                "--total",
                "1",
                "--output-dir",
                str(batch_out),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(p1.returncode, 0, p1.stderr + p1.stdout)
        created = json.loads(p1.stdout)
        batch_id = created["batch"]["id"]

        p2 = subprocess.run(
            [sys.executable, str(asynx_py), "batch", "poll", batch_id],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(p2.returncode, 0, p2.stderr + p2.stdout)
        polled = json.loads(p2.stdout)
        self.assertTrue(polled["ok"])
        self.assertEqual(polled["batch"]["status"], "completed")

    def test_cli_end_to_end_with_login_svr_session_config(self) -> None:
        from src.build_skills import build_skill
        build_skill("image")

        self.login_server.seed_user("cli_user", "secret_123", "CLI")
        login_and_provision("cli_user", "secret_123", login_svr_url=self.login_url)
        self.gateway_server.asset_data["task_1:0"] = b"\x89PNG\r\n\x1a\nFAKE"

        asynx_py = REPO_ROOT / "skills" / "asynx-image-pro" / "scripts" / "asynx.py"
        env = {
            **os.environ,
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CACHE_PATH": str(self.cache_file),
            "ASYNX_LOGIN_SVR_URL": self.login_url,
            "ASYNX_INITIAL_POLL_DELAY_SECONDS": "0.01",
        }
        proc = subprocess.run(
            [sys.executable, str(asynx_py), "doctor", "--verify"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertTrue(doc["ok"])
        self.assertTrue(doc["verification"]["ok"])
        self.assertEqual(doc["verification"]["partner_identity"]["api_key"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
