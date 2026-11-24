"""/api/auth/status distinguishes an invalid credential from a slow or unreachable service (PR #10, 11-24 P1-2)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))

from shared.asxlib import ui_server  # noqa: E402
from shared.asxlib.errors import AsxError  # noqa: E402

CONTEXT = {"origin": "https://dev.example.test"}


def bridge_failed(message: str) -> AsxError:
    return AsxError(f"凭据桥接命令失败：{message}", code="need_login",
                    details={"reason_code": "bridge_failed", "bridge_error": message})


class TestAuthStatusStates(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ui_server.WorkbenchServer(
            mode="tools", source_path=None, output_path=None, output_dir=tempfile.mkdtemp(),
            static_dir=REPO_ROOT / "src" / "ui" / "image",
        )
        self.addCleanup(self.server.server_close)
        patch = mock.patch.object(ui_server, "load_credential_context", return_value=CONTEXT)
        patch.start()
        self.addCleanup(patch.stop)

    def status_when_session_fails(self, exc: AsxError) -> dict:
        with mock.patch.object(ui_server, "load_auth_session", side_effect=exc):
            return self.server.get_auth_status()

    def test_invalid_credentials_are_unauthorized(self) -> None:
        cases = [
            AsxError("未找到本机凭据上下文", code="need_login", details={"reason_code": "missing_context"}),
            AsxError("凭据桥接脚本已不存在", code="need_login", details={"reason_code": "bridge_missing"}),
            AsxError("缺少 skill_jwt 权限", code="scope_missing"),
            AsxError("账号不一致", code="jwt_invalid", details={"reason_code": "account_mismatch"}),
            bridge_failed("Authorization expired or revoked; reconnect explicitly."),
            bridge_failed("Agent/site/account binding mismatch. No alternate credential will be used."),
        ]
        for exc in cases:
            with self.subTest(exc=exc.message):
                data = self.status_when_session_fails(exc)
                self.assertEqual(data["state"], "unauthorized")
                self.assertFalse(data["authenticated"])
                self.assertEqual(data["error"]["message"], exc.message, "the specific reason is kept")

    def test_connection_failures_keep_the_authorization(self) -> None:
        cases = [
            AsxError("凭据桥接命令超时", code="need_login", details={"reason_code": "bridge_timeout"}),
            bridge_failed("Identity verification failed; check connectivity. No operation was forwarded."),
            bridge_failed("JWT request failed (HTTP 503)."),
            bridge_failed("fetch failed"),
            bridge_failed("Non-JSON HTTP 502; check host connectivity/security. Do not retry with browser identities."),
            bridge_failed("connect ECONNREFUSED 127.0.0.1:443"),
            bridge_failed("getaddrinfo ENOTFOUND dev.plaiers.ai"),
            AsxError("无法连接 Plaiers 服务", code="network_error"),
            AsxError("Plaiers 服务 HTTP 502", code="sagein_http_502"),
        ]
        for exc in cases:
            with self.subTest(exc=exc.message):
                data = self.status_when_session_fails(exc)
                self.assertEqual(data["state"], "unavailable")
                self.assertEqual(data["error"]["message"], exc.message)

    def test_identity_query_failure_after_a_valid_session(self) -> None:
        session = mock.Mock(origin=CONTEXT["origin"], base_url="https://gw.example.test", asynx={})
        cases = {
            "unavailable": AsxError("网络连接失败：timed out", code="network_error"),
            "unauthorized": AsxError("Asset Gateway 拒绝了当前 JWT", code="need_login", http_status=401,
                                     details={"reason_code": "gateway_rejected_jwt"}),
        }
        for expected, exc in cases.items():
            with self.subTest(expected=expected), \
                    mock.patch.object(ui_server, "load_auth_session", return_value=session), \
                    mock.patch.object(ui_server.AsynxClient, "partner_identity", side_effect=exc):
                data = self.server.get_auth_status()
                self.assertEqual(data["state"], expected)
                self.assertEqual(data["authenticated"], expected == "unavailable")
                self.assertEqual(data["identity_error"]["message"], exc.message)

    def test_authorized_with_identity(self) -> None:
        session = mock.Mock(origin=CONTEXT["origin"], base_url="https://gw.example.test", asynx={})
        identity = {"subject": "account-42", "budget": {"remaining_amount": "1.915"}}
        with mock.patch.object(ui_server, "load_auth_session", return_value=session), \
                mock.patch.object(ui_server.AsynxClient, "partner_identity", return_value=(identity, None)):
            data = self.server.get_auth_status()
        self.assertEqual(data["state"], "authorized")
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["identity"], identity)


if __name__ == "__main__":
    unittest.main()
