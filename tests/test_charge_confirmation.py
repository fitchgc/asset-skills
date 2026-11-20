"""Issue 07: per-request charge confirmation (Asynx quote). Plaiers-side (玩造币) steps wait on Q4.

Nothing is submitted without a confirmation of this request's quote. A changed price or currency asks
again. A missing quote stops unless the user explicitly confirms without one (logged). A 5xx after
submission is never re-sent automatically; `task recover` reuses the same idempotency key.
"""

from __future__ import annotations

import json
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
            "ASYNX_PARTNER_TOKEN": "pt_charge",
            "ASYNX_BASE_URL": url,
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
        self.assertIn("不代表同意付费", error["message"])
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
