from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "shared" / "vendor"))

from shared.asxlib.auth import save_auth_session
from shared.asxlib.ui_server import WorkbenchServer
from tests.fake_gateway import run_fake_gateway
from tests.fake_login_server import make_test_jwt, run_fake_login_server


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else {}


class TestLoginWorkbenchHTTP(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.json"
        self.state_db = Path(self.temp_dir) / "state.db"
        self.cache_file = Path(self.temp_dir) / "models.json"
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)
        os.environ["ASYNX_STATE_PATH"] = str(self.state_db)
        os.environ["ASYNX_CACHE_PATH"] = str(self.cache_file)

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
            "ASYNX_NO_LOGIN_UI",
            "ASYNX_NO_OPEN_BROWSER",
        ):
            os.environ.pop(var, None)

    def test_auth_endpoints_lifecycle_and_login_mode_shutdown(self) -> None:
        server = WorkbenchServer(
            mode="login",
            source_path=None,
            output_path=None,
            output_dir=self.temp_dir,
            static_dir=REPO_ROOT / "src" / "ui" / "common",
            metadata={"login_svr_url": self.login_url},
        )
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            headers = {"X-Asx-Session-Token": server.session_token}

            # 0. Missing session token is rejected
            unauth_status, _ = _http_json(f"{base}/api/auth/status")
            self.assertEqual(unauth_status, 401)

            # 1. Initial auth status is unauthenticated
            st_code, st_body = _http_json(f"{base}/api/auth/status", headers=headers)
            self.assertEqual(st_code, 200)
            self.assertTrue(st_body["ok"])
            self.assertFalse(st_body["authenticated"])
            self.assertEqual(st_body["login_svr_url"], self.login_url)

            # 2. Username check validates format and proxies to login_svr
            bad_code, bad_body = _http_json(
                f"{base}/api/auth/check-username?username=A", headers=headers
            )
            self.assertEqual(bad_code, 400)
            self.assertFalse(bad_body["ok"])

            ok_code, ok_body = _http_json(
                f"{base}/api/auth/check-username?username=new_creator", headers=headers
            )
            self.assertEqual(ok_code, 200)
            self.assertTrue(ok_body["available"])

            # 3. Register succeeds, persists config, calls /v1/partner/identity, and triggers shutdown
            reg_code, reg_body = _http_json(
                f"{base}/api/auth/register",
                method="POST",
                headers=headers,
                body={
                    "username": "new_creator",
                    "password": "password123",
                    "nickname": "Creator One",
                    "login_svr_url": self.login_url,
                },
            )
            self.assertEqual(reg_code, 200)
            self.assertTrue(reg_body["ok"])
            self.assertTrue(reg_body["authenticated"])
            self.assertEqual(reg_body["user"]["username"], "new_creator")
            self.assertEqual(reg_body["identity"]["api_key"]["status"], "active")
            self.assertTrue(server.shutdown_event.wait(timeout=3.0))

            # Config saved on disk
            saved = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.assertIn("token", saved)
            self.assertIn("refreshToken", saved)
            old_token = saved["token"]
            old_rt = saved["refreshToken"]

            # 4. Authenticated status returns user + identity
            st2_code, st2_body = _http_json(f"{base}/api/auth/status", headers=headers)
            self.assertEqual(st2_code, 200)
            self.assertTrue(st2_body["authenticated"])
            self.assertEqual(st2_body["user"]["nickname"], "Creator One")
            self.assertEqual(st2_body["identity"]["budget"]["remaining_amount"], "50.00000000")

            # 5. Change password atomically rotates token + refreshToken
            cp_code, cp_body = _http_json(
                f"{base}/api/auth/change-password",
                method="POST",
                headers=headers,
                body={"oldPassword": "password123", "newPassword": "password456"},
            )
            self.assertEqual(cp_code, 200)
            self.assertTrue(cp_body["ok"])
            rotated = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.assertNotEqual(rotated["token"], old_token)
            self.assertNotEqual(rotated["refreshToken"], old_rt)

            # 6. Logout clears token and refreshToken
            lo_code, lo_body = _http_json(
                f"{base}/api/auth/logout", method="POST", headers=headers, body={}
            )
            self.assertEqual(lo_code, 200)
            self.assertTrue(lo_body["ok"])
            after_logout = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.assertNotIn("token", after_logout)
            self.assertNotIn("refreshToken", after_logout)

            # 7. Login with invalid password surfaces 1003 clearly, then valid password succeeds
            bad_login_code, bad_login = _http_json(
                f"{base}/api/auth/login",
                method="POST",
                headers=headers,
                body={"username": "new_creator", "password": "wrong_password"},
            )
            self.assertEqual(bad_login_code, 400)
            self.assertEqual(bad_login["error"]["details"]["login_svr_code"], 1003)

            login_code, login_body = _http_json(
                f"{base}/api/auth/login",
                method="POST",
                headers=headers,
                body={"username": "new_creator", "password": "password456"},
            )
            self.assertEqual(login_code, 200)
            self.assertTrue(login_body["authenticated"])
            self.assertEqual(login_body["user"]["username"], "new_creator")

            # 8. Locked (12) and deleted (15) accounts return clear error codes
            self.login_server.seed_user("locked_u", "password456", locked=True)
            locked_code, locked_body = _http_json(
                f"{base}/api/auth/login",
                method="POST",
                headers=headers,
                body={"username": "locked_u", "password": "password456"},
            )
            self.assertEqual(locked_code, 400)
            self.assertEqual(locked_body["error"]["code"], "account_locked")
            self.assertEqual(locked_body["error"]["details"]["login_svr_code"], 12)

            self.login_server.seed_user("deleted_u", "password456", deleted=True)
            del_code, del_body = _http_json(
                f"{base}/api/auth/login",
                method="POST",
                headers=headers,
                body={"username": "deleted_u", "password": "password456"},
            )
            self.assertEqual(del_code, 400)
            self.assertEqual(del_body["error"]["code"], "account_deleted")
            self.assertEqual(del_body["error"]["details"]["login_svr_code"], 15)
        finally:
            server.shutdown()
            server.server_close()

    def test_login_page_account_bar_and_skill_bundling(self) -> None:
        server = WorkbenchServer(
            mode="login",
            source_path=None,
            output_path=None,
            output_dir=self.temp_dir,
            static_dir=None,
            metadata={"login_svr_url": self.login_url},
        )
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"{base}/?token={server.session_token}") as resp:
                self.assertEqual(resp.status, 200)
                login_html = resp.read().decode("utf-8")
            self.assertIn("/api/auth/login", login_html)
            self.assertIn("/api/auth/register", login_html)
            self.assertIn("/api/auth/check-username", login_html)
            self.assertIn("^[a-z0-9_]{3,32}$", login_html)
            self.assertIn("login_svr_url", login_html)
            self.assertIn("1003", login_html)

            with urllib.request.urlopen(f"{base}/account-bar.js") as resp:
                self.assertEqual(resp.status, 200)
                account_js = resp.read().decode("utf-8")
            self.assertIn("/api/auth/status", account_js)
            self.assertIn("/api/auth/change-password", account_js)
            self.assertIn("/api/auth/logout", account_js)
        finally:
            server.shutdown()
            server.server_close()

        for ui_rel in ("src/ui/image/index.html", "src/ui/video/index.html", "src/ui/3d/index.html"):
            content = (REPO_ROOT / ui_rel).read_text(encoding="utf-8")
            self.assertIn("account-bar.js", content, f"{ui_rel} must load account-bar.js")
            self.assertIn("AsxAccountBar", content, f"{ui_rel} must mount AsxAccountBar")

        from src.build_skills import build_skill
        for mod_key, skill_name in (
            ("image", "asynx-image-pro"),
            ("audio", "asynx-audio"),
            ("video", "asynx-video"),
            ("3d", "asynx-3d"),
        ):
            skill_dir = build_skill(mod_key)
            ui_dir = skill_dir / "scripts" / "asxlib" / "ui"
            self.assertTrue((ui_dir / "login.html").is_file(), f"{skill_name} missing login.html")
            self.assertTrue((ui_dir / "account-bar.js").is_file(), f"{skill_name} missing account-bar.js")


class TestLoginWorkbenchCLIResumption(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill
        for key in ("image", "audio", "video", "3d"):
            build_skill(key)

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.json"
        self.state_db = Path(self.temp_dir) / "state.db"
        self.cache_file = Path(self.temp_dir) / "models.json"
        self.gateway_server, self.gateway_url, _ = run_fake_gateway()
        self.login_server, self.login_url, _ = run_fake_login_server(self.gateway_url)
        self.base_env = {
            **os.environ,
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CACHE_PATH": str(self.cache_file),
            "ASYNX_LOGIN_SVR_URL": self.login_url,
            "ASYNX_INITIAL_POLL_DELAY_SECONDS": "0.01",
            "ASYNX_NO_OPEN_BROWSER": "1",
        }
        self.base_env.pop("ASYNX_NO_LOGIN_UI", None)
        self.base_env.pop("ASYNX_PARTNER_TOKEN", None)
        self.base_env.pop("CI", None)

    def tearDown(self) -> None:
        self.login_server.shutdown()
        self.login_server.server_close()
        self.gateway_server.shutdown()
        self.gateway_server.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _skill_script(skill_name: str) -> Path:
        return REPO_ROOT / "skills" / skill_name / "scripts" / "asynx.py"

    @staticmethod
    def _wait_for_workbench_url(proc: subprocess.Popen[str], timeout: float = 10.0) -> tuple[str, str]:
        assert proc.stderr is not None
        deadline = time.time() + timeout
        lines: list[str] = []
        while time.time() < deadline:
            line = proc.stderr.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            lines.append(line)
            m = re.search(r"http://127\.0\.0\.1:\d+/\?token=([a-f0-9]+)", line)
            if m:
                base_url = line[m.start():].split("/?")[0]
                return base_url, m.group(1)
        raise AssertionError(f"Timed out waiting for workbench URL; stderr={''.join(lines)!r}")

    def test_no_login_ui_fails_fast_and_login_whoami_logout_across_all_skills(self) -> None:
        self.login_server.seed_user("cli_all", "pass_1234", "CLI All")
        for skill_name in ("asynx-image-pro", "asynx-audio", "asynx-video", "asynx-3d"):
            script = self._skill_script(skill_name)
            # Both flag placements fail fast with need_login
            for cmd in ([sys.executable, str(script), "--no-login-ui", "models"],
                        [sys.executable, str(script), "models", "--no-login-ui"]):
                proc = subprocess.run(cmd, capture_output=True, text=True, env=self.base_env, timeout=10)
                self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
                payload = json.loads(proc.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "need_login")

        # ASYNX_NO_LOGIN_UI=1 also fails fast
        env_no_ui = {**self.base_env, "ASYNX_NO_LOGIN_UI": "1"}
        proc_env = subprocess.run(
            [sys.executable, str(self._skill_script("asynx-audio")), "whoami"],
            capture_output=True,
            text=True,
            env=env_no_ui,
            timeout=10,
        )
        self.assertEqual(proc_env.returncode, 2)
        self.assertEqual(json.loads(proc_env.stdout)["error"]["code"], "need_login")

        # Explicit login --no-open on asynx-audio -> whoami on asynx-3d -> logout on asynx-video
        proc_login = subprocess.Popen(
            [sys.executable, str(self._skill_script("asynx-audio")), "login", "--no-open"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.base_env,
        )
        wb_url, wb_tok = self._wait_for_workbench_url(proc_login)
        code, body = _http_json(
            f"{wb_url}/api/auth/login",
            method="POST",
            headers={"X-Asx-Session-Token": wb_tok},
            body={"username": "cli_all", "password": "pass_1234"},
        )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        out, err = proc_login.communicate(timeout=10)
        self.assertEqual(proc_login.returncode, 0, err + out)
        login_out = json.loads(out)
        self.assertTrue(login_out["ok"])
        self.assertTrue(login_out["authenticated"])
        self.assertEqual(login_out["user"]["username"], "cli_all")

        # whoami on another skill reads shared session
        who = subprocess.run(
            [sys.executable, str(self._skill_script("asynx-3d")), "whoami", "--no-login-ui"],
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(who.returncode, 0, who.stderr + who.stdout)
        who_data = json.loads(who.stdout)
        self.assertTrue(who_data["authenticated"])
        self.assertEqual(who_data["user"]["username"], "cli_all")
        self.assertEqual(who_data["identity"]["api_key"]["status"], "active")

        # logout clears credentials
        lo = subprocess.run(
            [sys.executable, str(self._skill_script("asynx-video")), "logout"],
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(lo.returncode, 0, lo.stderr + lo.stdout)
        self.assertTrue(json.loads(lo.stdout)["logged_out"])

        who_after = subprocess.run(
            [sys.executable, str(self._skill_script("asynx-image-pro")), "whoami", "--no-login-ui"],
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(who_after.returncode, 2)
        self.assertEqual(json.loads(who_after.stdout)["error"]["code"], "need_login")

    def test_interrupted_generate_models_and_doctor_verify_resume_after_login_workbench(self) -> None:
        # 1. Missing credentials on `image generate` -> register in Login Workbench -> task completes
        out_dir = Path(self.temp_dir) / "gen_out"
        self.gateway_server.asset_data["task_1:0"] = b"\x89PNG\r\n\x1a\nRESUMED_PNG"
        proc_gen = subprocess.Popen(
            [
                sys.executable,
                str(self._skill_script("asynx-image-pro")),
                "generate",
                "--prompt",
                "resumed warrior",
                "--output-dir",
                str(out_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.base_env,
        )
        wb_url, wb_tok = self._wait_for_workbench_url(proc_gen)
        reg_code, _ = _http_json(
            f"{wb_url}/api/auth/register",
            method="POST",
            headers={"X-Asx-Session-Token": wb_tok},
            body={"username": "resumed_user", "password": "password123", "nickname": "Resumed"},
        )
        self.assertEqual(reg_code, 200)
        out, err = proc_gen.communicate(timeout=15)
        self.assertEqual(proc_gen.returncode, 0, err + out)
        gen_payload = json.loads(out)
        self.assertTrue(gen_payload["ok"])
        self.assertEqual(gen_payload["status"], "succeeded")
        self.assertEqual(Path(gen_payload["files"][0]).read_bytes(), b"\x89PNG\r\n\x1a\nRESUMED_PNG")

        # 2. Expired token + invalid refreshToken on `audio models` -> login -> models succeeds
        os.environ["ASYNX_CONFIG_PATH"] = str(self.config_file)
        try:
            save_auth_session(
                {
                    "token": make_test_jwt(exp=int(time.time()) - 100),
                    "refreshToken": "rt_revoked_invalid",
                    "asynx": dict(self.login_server.asynx_config),
                    "user": {"id": "u1", "username": "resumed_user"},
                },
                login_svr_url=self.login_url,
            )
        finally:
            os.environ.pop("ASYNX_CONFIG_PATH", None)

        proc_models = subprocess.Popen(
            [sys.executable, str(self._skill_script("asynx-audio")), "models"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.base_env,
        )
        wb_url2, wb_tok2 = self._wait_for_workbench_url(proc_models)
        l_code, _ = _http_json(
            f"{wb_url2}/api/auth/login",
            method="POST",
            headers={"X-Asx-Session-Token": wb_tok2},
            body={"username": "resumed_user", "password": "password123"},
        )
        self.assertEqual(l_code, 200)
        m_out, m_err = proc_models.communicate(timeout=15)
        self.assertEqual(proc_models.returncode, 0, m_err + m_out)
        m_payload = json.loads(m_out)
        self.assertTrue(m_payload["ok"])
        self.assertGreater(len(m_payload["models"]), 0)

        # 3. Missing credentials on `video doctor --verify` -> login -> doctor passes
        self.config_file.unlink()
        proc_doc = subprocess.Popen(
            [sys.executable, str(self._skill_script("asynx-video")), "doctor", "--verify"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.base_env,
        )
        wb_url3, wb_tok3 = self._wait_for_workbench_url(proc_doc)
        d_code, _ = _http_json(
            f"{wb_url3}/api/auth/login",
            method="POST",
            headers={"X-Asx-Session-Token": wb_tok3},
            body={"username": "resumed_user", "password": "password123"},
        )
        self.assertEqual(d_code, 200)
        d_out, d_err = proc_doc.communicate(timeout=15)
        self.assertEqual(proc_doc.returncode, 0, d_err + d_out)
        d_payload = json.loads(d_out)
        self.assertTrue(d_payload["ok"])
        self.assertTrue(d_payload["verification"]["ok"])

        # 4. doctor --verify --no-login-ui fails fast without starting workbench
        self.config_file.unlink()
        doc_no_ui = subprocess.run(
            [sys.executable, str(self._skill_script("asynx-3d")), "doctor", "--verify", "--no-login-ui"],
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(doc_no_ui.returncode, 2)
        doc_no_ui_data = json.loads(doc_no_ui.stdout)
        self.assertFalse(doc_no_ui_data["ok"])
        self.assertEqual(doc_no_ui_data["verification"]["error"]["code"], "need_login")

        # 5. Cancelling login workbench on interrupted command exits cleanly with need_login
        proc_cancel = subprocess.Popen(
            [sys.executable, str(self._skill_script("asynx-image-pro")), "models"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.base_env,
        )
        wb_url4, wb_tok4 = self._wait_for_workbench_url(proc_cancel)
        c_code, _ = _http_json(
            f"{wb_url4}/api/cancel",
            method="POST",
            headers={"X-Asx-Session-Token": wb_tok4},
            body={},
        )
        self.assertEqual(c_code, 200)
        c_out, c_err = proc_cancel.communicate(timeout=10)
        self.assertEqual(proc_cancel.returncode, 2, c_err + c_out)
        self.assertEqual(json.loads(c_out)["error"]["code"], "need_login")
