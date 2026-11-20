"""Issue 07: per-request charge confirmation (Asynx quote → user confirmation → submit; 积分, no 玩造币).

Nothing is submitted without a confirmation of this request's quote. A changed price or currency asks
again. A missing quote stops unless the user explicitly confirms without one (logged). A 5xx after
submission is never re-sent automatically; `task recover` reuses the same idempotency key.
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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fake_gateway import run_fake_gateway  # noqa: E402
from tests.bridge_env import bridge_env

IMAGE = "plaiers-asynx-image-pro"
AUDIO = "plaiers-asynx-audio"


class ChargeConfirmationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill

        for modality in ("image", "audio", "video", "3d"):
            build_skill(modality)

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.server, url, _thread = run_fake_gateway()
        self.server.asset_data["task_1:0"] = b"\x89PNG\r\n\x1a\n"
        self.log = self.temp / "requests.jsonl"
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.temp / "home"),
            "ASYNX_STATE_PATH": str(self.temp / "state.db"),
            "ASYNX_CONFIG_PATH": str(self.temp / "config.json"),
            **bridge_env(self, Path(self.temp), url),
            "ASYNX_LOG_PATH": str(self.log),
        }

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.temp, ignore_errors=True)

    def cli(self, skill: str, *args: str) -> tuple[int, dict[str, Any]]:
        script = REPO_ROOT / "skills" / skill / "scripts" / "asynx.py"
        proc = subprocess.run([sys.executable, "-S", str(script), *args], capture_output=True, text=True,
                              env=self.env, cwd=self.temp, timeout=120)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def submit_posts(self) -> list[dict[str, str]]:
        return [headers for method, path, headers in self.server.received_requests if method == "POST" and path == "/v1/tasks"]

    def image(self, *extra: str) -> tuple[int, dict[str, Any]]:
        return self.cli(IMAGE, "generate", "--prompt", "a lantern", "--detach", "--output-dir", str(self.temp / "out"), *extra)

    def charge_events(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]
        return [row for row in rows if row.get("event") == "charge_consent"]

    def test_no_confirmation_submits_nothing_and_shows_quote_and_budget(self) -> None:
        code, out = self.image()
        self.assertEqual(code, 2, out)
        error = out["error"]
        self.assertEqual(error["code"], "confirmation_required")
        details = error["details"]
        self.assertEqual((details["currency"], details["total_amount"], details["units"]), ("USD", "0.12000000", 1))
        self.assertEqual(details["budget"], {"remaining_amount": "50.00000000"})
        self.assertEqual(details["confirm_args"], ["--confirmed-quote", "USD:0.12000000"])
        self.assertFalse(details["submitted"])
        self.assertIn("不代表同意消耗积分", error["message"])
        self.assertEqual(self.submit_posts(), [])
        # Other modalities use the same gate.
        code, out = self.cli(AUDIO, "sfx", "--prompt", "clash", "--duration", "1.0", "--detach", "--output-dir", str(self.temp / "a"))
        self.assertEqual(out["error"]["code"], "confirmation_required")
        self.assertEqual(self.submit_posts(), [])

    def test_matching_confirmation_submits_once_and_is_recorded(self) -> None:
        _, first = self.image()
        code, out = self.image(*first["error"]["details"]["confirm_args"])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.submit_posts()), 1)
        events = self.charge_events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["confirmation"], events[0]["total_amount"]), ("amount", "0.12000000"))
        # A plain amount at or above the quote also counts.
        code, _ = self.image("--confirmed-quote", "0.5")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.submit_posts()), 2)

    def test_changed_price_or_currency_asks_again(self) -> None:
        self.server.quote_overrides = {"charge_amount": "0.30000000", "estimated_amount": "0.30000000"}
        code, out = self.image("--confirmed-quote", "USD:0.12")
        self.assertEqual(code, 2)
        self.assertEqual((out["error"]["code"], out["error"]["details"]["reason"]), ("confirmation_required", "price_changed"))
        code, out = self.image("--confirmed-quote", "EUR:5")
        self.assertEqual(out["error"]["details"]["reason"], "currency_changed")
        code, out = self.image("--confirmed-quote", "q_expired")
        self.assertEqual(out["error"]["details"]["reason"], "quote_mismatch")
        self.assertEqual(self.submit_posts(), [])

    def test_quote_id_confirmation(self) -> None:
        self.server.quote_overrides = {"quote_id": "q_123"}
        _, out = self.image()
        self.assertEqual(out["error"]["details"]["confirm_args"], ["--confirmed-quote", "q_123"])
        code, out = self.image("--confirmed-quote", "q_123")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.submit_posts()), 1)

    def test_zero_charge_needs_no_confirmation(self) -> None:
        self.server.quote_overrides = {"charge_amount": "0", "estimated_amount": "0"}
        code, out = self.image()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.submit_posts()), 1)

    def test_quote_unavailable_stops_unless_explicitly_confirmed(self) -> None:
        self.server.quote_error_status = 404
        code, out = self.image("--confirmed-quote", "USD:1")
        self.assertEqual(code, 2)
        self.assertEqual(out["error"]["code"], "quote_unavailable")
        self.assertEqual(out["error"]["details"]["confirm_args"], ["--confirm-without-quote"])
        self.assertEqual(self.submit_posts(), [])
        code, out = self.image("--confirm-without-quote")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.submit_posts()), 1)
        events = self.charge_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["confirmed_without_quote"])
        self.assertIsNone(events[0]["quote"])

    def test_without_quote_flag_does_not_bypass_an_available_quote(self) -> None:
        code, out = self.image("--confirm-without-quote")
        self.assertEqual(out["error"]["code"], "confirmation_required")
        self.assertEqual(self.submit_posts(), [])

    def test_5xx_after_submission_is_not_resent_and_recover_reuses_the_key(self) -> None:
        self.server.fail_next_submit_statuses = [503]
        code, out = self.image("--confirmed-quote", "USD:1")
        self.assertNotEqual(code, 0)
        posts = self.submit_posts()
        self.assertEqual(len(posts), 1, "no automatic resubmission after a 5xx")
        key = posts[0]["Idempotency-Key"]
        details = out["error"]["details"]
        self.assertEqual(details["idempotency_key"], key)
        self.assertEqual(details["next_action_args"][:2], ["task", "recover"])
        code, recovered = self.cli(IMAGE, *details["next_action_args"])
        self.assertEqual(code, 0, recovered)
        posts = self.submit_posts()
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[1]["Idempotency-Key"], key, "recover reuses the same idempotency key")
        self.assertEqual(len(self.server.submissions), 1)

    def test_rerun_with_same_idempotency_key_does_not_submit_twice(self) -> None:
        args = ("--confirmed-quote", "USD:1", "--idempotency-key", "asx-charge-test-key-0001")
        self.assertEqual(self.image(*args)[0], 0)
        self.assertEqual(self.image(*args)[0], 0)
        self.assertEqual(len(self.server.submissions), 1)

    def test_batch_confirms_the_total_before_creating_anything(self) -> None:
        batch = ("batch", "create", "--prompt", "tile", "--total", "3", "--output-dir", str(self.temp / "b"))
        code, out = self.cli(IMAGE, *batch)
        self.assertEqual(code, 2)
        details = out["error"]["details"]
        self.assertEqual((details["units"], details["total_amount"]), (3, "0.36000000"))
        self.assertEqual(self.cli(IMAGE, "batch", "list")[1]["batches"], [])
        # Confirming only one item's price is not enough for three.
        code, out = self.cli(IMAGE, *batch, "--confirmed-quote", "USD:0.12")
        self.assertEqual(out["error"]["details"]["reason"], "price_changed")
        self.assertEqual(self.submit_posts(), [])
        code, out = self.cli(IMAGE, *batch, *details["confirm_args"])
        self.assertIn(code, (0, 4), out)
        self.assertEqual(out["charge_consent"]["total_amount"], "0.36000000")
        self.assertGreaterEqual(len(self.submit_posts()), 1)


if __name__ == "__main__":
    unittest.main()


class WorkbenchChargeConfirmationTest(unittest.TestCase):
    """The Local UI Workbench and asset-manager cloud actions use the same quote → confirm → submit gate."""

    def setUp(self) -> None:
        from tests.interpreter_pillow import add_interpreter_pillow

        add_interpreter_pillow()
        import PIL.Image

        from shared.asxlib.client import AsynxClient

        self.temp = Path(tempfile.mkdtemp())
        self._env = {k: os.environ.get(k) for k in ("ASYNX_STATE_PATH", "ASYNX_CONFIG_PATH", "ASYNX_LOG_PATH")}
        os.environ["ASYNX_STATE_PATH"] = str(self.temp / "state.db")
        os.environ["ASYNX_CONFIG_PATH"] = str(self.temp / "config.json")
        os.environ["ASYNX_LOG_PATH"] = str(self.temp / "requests.jsonl")
        self.gateway, url, _ = run_fake_gateway()
        self.client = AsynxClient(url, "pt_ui")
        self.source = self.temp / "source.png"
        PIL.Image.new("RGBA", (32, 32), (0, 128, 255, 255)).save(self.source)
        self.gateway.asset_data["task_1:0"] = self.source.read_bytes()
        self.gateway.asset_mimes["task_1:0"] = "image/png"

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.temp, ignore_errors=True)

    def submit_posts(self) -> int:
        return sum(1 for m, p, _ in self.gateway.received_requests if (m, p) == ("POST", "/v1/tasks"))

    def test_region_edit_asks_first_then_submits_after_confirmation(self) -> None:
        import threading
        import urllib.error
        import urllib.request

        from shared.asxlib.ui_server import WorkbenchServer

        server = WorkbenchServer(mode="region_edit", source_path=str(self.source), output_path=None,
                                 output_dir=str(self.temp), static_dir=REPO_ROOT / "src" / "ui" / "image",
                                 client=self.client)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)

        def post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/submit", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-Asx-Session-Token": server.session_token},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        body = {"prompt": "make it red", "model": "gpt-image-2.5-sunburst", "keep_alive": True,
                "annotations": [{"id": 1, "type": "rect", "instruction": "red"}]}
        status, out = post(body)
        self.assertEqual(status, 409, out)
        self.assertEqual(out["error"]["code"], "confirmation_required")
        self.assertEqual(self.submit_posts(), 0)
        confirm = out["error"]["details"]["confirm_args"][1]
        self.gateway.quote_overrides = {"charge_amount": "0.50000000", "estimated_amount": "0.50000000"}
        status, out = post({**body, "confirmed_quote": confirm})
        self.assertEqual((status, out["error"]["details"]["reason"]), (409, "price_changed"))
        self.assertEqual(self.submit_posts(), 0)
        status, out = post({**body, "confirmed_quote": out["error"]["details"]["confirm_args"][1]})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.submit_posts(), 1)

    def test_manager_cloud_action_needs_confirmation_local_action_does_not(self) -> None:
        from src.asset_manager import actions

        body = {"action": "image_edit", "source_path": str(self.source), "prompt": "add crown"}
        with self.assertRaises(Exception) as ctx:
            actions.dispatch_manager_action(body, workspace_root=self.temp, require_client=lambda: self.client)
        self.assertEqual(getattr(ctx.exception, "code", None), "confirmation_required")
        self.assertEqual(self.submit_posts(), 0)
        confirm = ctx.exception.details["confirm_args"][1]
        res = actions.dispatch_manager_action({**body, "confirmed_quote": confirm}, workspace_root=self.temp,
                                              require_client=lambda: self.client)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.submit_posts(), 1)
        # A confirmation belongs to one request: the next action asks again.
        with self.assertRaises(Exception) as again:
            actions.dispatch_manager_action(body, workspace_root=self.temp, require_client=lambda: self.client)
        self.assertEqual(getattr(again.exception, "code", None), "confirmation_required")
        local = actions.dispatch_manager_action(
            {"action": "image_grid_slice", "source_path": str(self.source), "rows": 2, "columns": 2, "fps": 12},
            workspace_root=self.temp, require_client=lambda: self.client,
        )
        self.assertTrue(local["ok"])
        self.assertEqual(self.submit_posts(), 1)

    def test_pages_ask_the_user_before_resending(self) -> None:
        common = (REPO_ROOT / "src" / "ui" / "common" / "plaiers-ui.js").read_text(encoding="utf-8")
        self.assertIn("function plPostWithChargeConsent", common)
        self.assertIn("window.confirm(plChargeMessage(error))", common)
        self.assertIn("charge_declined", common)
        for page in ("image", "asset_manager"):
            html = (REPO_ROOT / "src" / "ui" / page / "index.html").read_text(encoding="utf-8")
            self.assertIn("plPostWithChargeConsent(", html, page)
