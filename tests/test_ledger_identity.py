"""PR #10 P1 (2026-11-20): cloud task state belongs to one identity (site + Agent + account).

Before the fix every installation of one OS user shared one task ledger and recovery re-sent whatever
unsubmitted request it found, with the caller's JWT and without asking for a quote. These tests drive
the built CLIs against a fake site, bridge and gateway: two identities under one HOME (default ledger
path), another site or Agent, one explicitly shared ledger, a copied ledger, failed submission and
recovery, batches, cancel/sync, records left by earlier releases, a changed gateway, and a race.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fake_gateway import run_fake_gateway  # noqa: E402
from tests.fake_sagein_server import install_fake_context, run_fake_sagein_server, write_binding  # noqa: E402

IMAGE = "plaiers-asynx-image-pro"
PRIVATE_PROMPT = "PRIVATE REQUEST FROM ACCOUNT A"
CONFIRM = ("--confirmed-quote", "USD:1")


def _subject(headers: dict[str, str]) -> str:
    segment = headers["X-Asynx-Partner-Token"].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))["sub"]


class LedgerIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill

        build_skill("image")

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.gateway, self.gateway_url, _ = run_fake_gateway()
        self.site, self.origin, _ = run_fake_sagein_server(self.gateway_url)
        for server in (self.gateway, self.site):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.home = self.temp / "home"
        self.a = self.identity("a", agent="agent-a", account="account-a")
        self.b = self.identity("b", agent="agent-b", account="account-b")

    # ---- helpers ---------------------------------------------------------------------------------

    def identity(self, name: str, *, agent: str, account: str, origin: str | None = None,
                 site: Any = None, pin: bool = True) -> dict[str, str]:
        """One installation: its own context, Agent, account and credential directory; shared HOME and,
        unless a test sets it, no ASYNX_STATE_PATH, so the default ledger location is used."""
        pat = "sagein_pat_" + hashlib.sha256(f"{name}:{account}".encode()).hexdigest()
        (site or self.site).pats[pat] = account
        config_dir = self.temp / f"credentials-{name}"
        context, _ = install_fake_context(
            self.temp / f"install-{name}", origin or self.origin, agent_id=agent, config_dir=config_dir,
            account_id=account if pin else None, binding=False,
        )
        write_binding(config_dir, agent, account, pat)
        return {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.home),
            "ASYNX_CONFIG_PATH": str(self.temp / "config.json"),
            "ASYNX_LOG_PATH": str(self.temp / f"requests-{name}.jsonl"),
            "PLAIERS_SKILL_CONTEXT": str(context),
        }

    def cli(self, env: dict[str, str], *args: str) -> tuple[int, dict[str, Any]]:
        script = REPO_ROOT / "skills" / IMAGE / "scripts" / "asynx.py"
        proc = subprocess.run([sys.executable, "-S", str(script), *args], capture_output=True, text=True,
                              env=env, cwd=self.temp, timeout=120)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def generate(self, env: dict[str, str], prompt: str = PRIVATE_PROMPT, *extra: str) -> tuple[int, dict[str, Any]]:
        return self.cli(env, "generate", "--prompt", prompt, "--detach", "--output-dir", str(self.temp / "out"),
                        *CONFIRM, *extra)

    def failed_submission(self, env: dict[str, str]) -> str:
        """A confirmed request whose first submission got a 503: a local task without a remote ID."""
        self.gateway.fail_next_submit_statuses = [503]
        code, out = self.generate(env)
        self.assertNotEqual(code, 0, out)
        return str(out["error"]["details"]["local_id"])

    def attempts(self) -> list[tuple[str, str, str]]:
        """(JWT sub, prompt, idempotency key) of every POST /v1/tasks the gateway received."""
        return [(_subject(h), body["input"]["prompt"], h["Idempotency-Key"]) for h, body in self.gateway.submit_attempts]

    def requests(self, method: str, prefix: str) -> list[str]:
        return [_subject(h) for m, path, h in self.gateway.received_requests if m == method and path.startswith(prefix)]

    def default_ledgers(self) -> list[Path]:
        return sorted((self.home / ".local" / "state" / "asx" / "identities").glob("*/state.db"))

    # ---- the reported scenario and its variants --------------------------------------------------

    def test_another_identity_cannot_recover_see_or_touch_a_failed_submission(self) -> None:
        local_id = self.failed_submission(self.a)
        first = self.attempts()
        self.assertEqual([(sub, prompt) for sub, prompt, _ in first], [("account-a", PRIVATE_PROMPT)])
        others = {
            "other agent and account": self.b,
            "same agent, other account, account not pinned": self.identity(
                "c", agent="agent-a", account="account-c", pin=False),
            "same account, other agent": self.identity("d", agent="agent-d", account="account-a"),
        }
        other_site, other_origin, _ = run_fake_sagein_server(self.gateway_url)
        self.addCleanup(other_site.server_close)
        self.addCleanup(other_site.shutdown)
        others["same agent and account names on another site"] = self.identity(
            "e", agent="agent-a", account="account-a", origin=other_origin, site=other_site)
        for label, env in others.items():
            with self.subTest(label):
                quotes = len(self.requests("POST", "/v1/tasks/quote"))
                code, out = self.cli(env, "task", "recover")
                self.assertEqual((code, out["tasks"]), (0, []), out)
                for command in (("task", "recover", local_id), ("task", "status", local_id),
                                ("task", "cancel", local_id), ("asset", "list", local_id),
                                ("task", "status", local_id, "--refresh")):
                    code, out = self.cli(env, *command)
                    self.assertEqual(out["error"]["code"], "task_not_found", (command, out))
                self.assertEqual(self.cli(env, "task", "list")[1]["tasks"], [])
                self.assertEqual(self.cli(env, "task", "poll")[1]["tasks"], [])
                self.assertEqual(self.cli(env, "recent")[1]["artifacts"], [])
                self.assertEqual(self.attempts(), first, "another identity submitted A's request")
                self.assertEqual(len(self.requests("POST", "/v1/tasks/quote")), quotes)
        # One ledger per identity under the shared HOME, each recording its owner.
        owners = set()
        for ledger in self.default_ledgers():
            row = sqlite3.connect(ledger).execute("SELECT origin, agent_id, account_id FROM ledger_owner").fetchone()
            owners.add(row)
        self.assertEqual(len(owners), 5)
        self.assertIn((self.origin, "agent-a", "account-a"), owners)

        # The owner still recovers its own confirmed request: same idempotency key, no new quote.
        quotes = len(self.requests("POST", "/v1/tasks/quote"))
        code, out = self.cli(self.a, "task", "recover")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["tasks"][0]["local_id"], local_id)
        self.assertEqual(self.attempts(), [first[0], first[0]])
        self.assertEqual(len(self.requests("POST", "/v1/tasks/quote")), quotes)

    def test_explicitly_shared_ledger_is_refused_for_another_identity(self) -> None:
        shared = {"ASYNX_STATE_PATH": str(self.temp / "shared-state.db")}
        a, b = {**self.a, **shared}, {**self.b, **shared}
        local_id = self.failed_submission(a)
        first = self.attempts()
        for command in (("task", "recover"), ("task", "recover", local_id), ("task", "list"), ("task", "poll"),
                        ("task", "cancel", local_id), ("task", "status", local_id), ("asset", "list", local_id),
                        ("recent",), ("batch", "list"), ("batch", "poll"), ("ledger", "status"),
                        ("wait", local_id)):
            code, out = self.cli(b, *command)
            self.assertEqual((code, out["error"]["code"]), (2, "ledger_owner_mismatch"), (command, out))
            self.assertNotIn("account-a", json.dumps(out))
            self.assertNotIn(PRIVATE_PROMPT, json.dumps(out))
        # B's own new request is not written into A's ledger either, and is not submitted.
        code, out = self.generate(b, "b wants a lantern")
        self.assertEqual((code, out["error"]["code"]), (2, "ledger_owner_mismatch"), out)
        self.assertEqual(self.attempts(), first)
        code, out = self.cli(a, "task", "recover", local_id)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.attempts(), [first[0], first[0]])

    def test_copied_ledger_does_not_transfer_tasks_or_confirmations(self) -> None:
        """Ownership is checked from what the ledger records, not from where the file sits."""
        local_id = self.failed_submission(self.a)
        first = self.attempts()
        (a_ledger,) = self.default_ledgers()
        self.cli(self.b, "task", "list")
        (b_ledger,) = [path for path in self.default_ledgers() if path != a_ledger]
        for suffix in ("", "-wal", "-shm"):
            Path(str(b_ledger) + suffix).unlink(missing_ok=True)
        with sqlite3.connect(a_ledger) as source, sqlite3.connect(b_ledger) as copy:
            source.backup(copy)
        code, out = self.cli(self.b, "task", "recover")
        self.assertEqual((code, out["error"]["code"]), (2, "ledger_owner_mismatch"), out)

        # Even with the owner record removed, and B claiming the records as its own, A's confirmation
        # does not become B's: the request is not submitted with B's JWT.
        b_ledger.unlink()
        unowned = self.temp / "unowned.db"
        with sqlite3.connect(a_ledger) as source, sqlite3.connect(unowned) as copy:
            source.backup(copy)
            copy.execute("DROP TABLE ledger_owner")
        code, out = self.cli({**self.b, "ASYNX_STATE_PATH": str(unowned)}, "task", "recover")
        self.assertEqual((code, out["error"]["code"]), (2, "ledger_owner_unknown"), out)
        code, out = self.cli(self.b, "ledger", "migrate", "--source", str(unowned), "--confirm-ownership")
        self.assertEqual(code, 0, out)
        code, out = self.cli(self.b, "task", "recover", local_id)
        self.assertEqual(code, 4, out)
        self.assertEqual(out["tasks"][0]["error"]["code"], "charge_consent_mismatch")
        self.assertEqual(out["tasks"][0]["error"]["details"]["reason"], "identity")
        self.assertEqual(self.attempts(), first)

    # ---- batches ----------------------------------------------------------------------------------

    def test_batch_is_resumed_only_by_its_owner(self) -> None:
        code, out = self.cli(self.a, "batch", "create", "--prompt", PRIVATE_PROMPT, "--total", "3", "--limit", "1",
                             "--output-dir", str(self.temp / "batch"), "--confirmed-quote", "USD:1")
        self.assertEqual(code, 0, out)
        batch_id = out["batch"]["id"]
        self.assertEqual(len(self.attempts()), 1)
        quotes = len(self.requests("POST", "/v1/tasks/quote"))
        for command in (("batch", "poll"), ("batch", "poll", batch_id), ("batch", "status", batch_id),
                        ("batch", "cancel", batch_id), ("batch", "resume", batch_id), ("batch", "wait", batch_id,
                                                                                      "--interval", "1")):
            code, out = self.cli(self.b, *command)
            self.assertEqual(out["error"]["code"], "batch_not_found", (command, out))
        self.assertEqual(self.cli(self.b, "batch", "list")[1]["batches"], [])
        self.assertEqual(len(self.attempts()), 1)
        shared = {"ASYNX_STATE_PATH": str(self.default_ledgers()[0])}
        code, out = self.cli({**self.b, **shared}, "batch", "poll", batch_id)
        self.assertEqual((code, out["error"]["code"]), (2, "ledger_owner_mismatch"), out)
        self.assertEqual(len(self.attempts()), 1)

        code, out = self.cli(self.a, "batch", "poll", batch_id, "--limit", "5")
        self.assertEqual(code, 0, out)
        attempts = self.attempts()
        self.assertEqual([sub for sub, _, _ in attempts], ["account-a"] * 3)
        self.assertEqual(sorted(key for _, _, key in attempts), [f"asx-{batch_id}-{n:06d}" for n in (1, 2, 3)])
        self.assertEqual(len(self.requests("POST", "/v1/tasks/quote")), quotes, "resuming a batch is not a new quote")

    # ---- cancel, sync, download -------------------------------------------------------------------

    def test_cancel_sync_and_download_stay_with_the_owner(self) -> None:
        self.gateway.asset_data["task_1:0"] = b"\x89PNG\r\n\x1a\n"
        self.gateway.default_submit_status = "running"
        code, out = self.generate(self.a)
        self.assertEqual(code, 0, out)
        local_id, remote_id = out["local_id"], out["task_id"]
        for command in (("task", "cancel", local_id), ("task", "cancel", remote_id), ("task", "poll", local_id),
                        ("task", "status", local_id, "--refresh"), ("asset", "list", remote_id)):
            code, out = self.cli(self.b, *command)
            self.assertEqual(out["error"]["code"], "task_not_found", (command, out))
        self.assertEqual(self.requests("POST", f"/v1/tasks/{remote_id}/cancel"), [])
        self.assertNotIn("account-b", self.requests("GET", f"/v1/tasks/{remote_id}"))

        self.gateway.tasks[remote_id].update(
            status="succeeded", result={"assets": [{"index": 0, "download_url": None}]})
        code, out = self.cli(self.a, "wait", local_id)
        self.assertEqual(code, 0, out)
        self.assertTrue(Path(out["files"][0]).is_file())
        self.assertEqual(set(self.requests("GET", f"/v1/tasks/{remote_id}")), {"account-a"})
        self.assertEqual(self.cli(self.b, "recent")[1]["artifacts"], [])
        self.assertEqual(len(self.cli(self.a, "recent")[1]["artifacts"]), 1)

    # ---- records written before ownership was recorded -------------------------------------------

    def legacy_ledger(self) -> tuple[Path, str]:
        """The single per-user ledger of earlier releases: no owner, no charge confirmation records."""
        from shared.asxlib.state import bind_remote_task, connect_db, create_task_intent, ensure_batch_schema

        path = self.home / ".local" / "state" / "asx" / "state.db"
        path.parent.mkdir(parents=True)
        self.gateway.tasks["task_legacy"] = {"id": "task_legacy", "task_type": "image.generate", "status": "running",
                                             "model": "m", "input": {}}
        connection = connect_db(path)
        body = {"model": "gpt-image-2.5-sunburst", "input": {"prompt": PRIVATE_PROMPT}}
        for local_id, key in (("local_never_sent", "asx-legacy-1"), ("local_sent", "asx-legacy-2")):
            create_task_intent(connection, operation="generate", model="gpt-image-2.5-sunburst", idempotency_key=key,
                               request=body, output_dir=str(self.temp / "legacy-out"), base_url=self.gateway_url,
                               local_id=local_id, status="submitting")
        bind_remote_task(connection, "local_sent", "task_legacy", status="running")
        ensure_batch_schema(connection)
        connection.execute(
            "INSERT INTO batches (id, operation, name, status, model, output_dir, template_json, created_at, updated_at) "
            "VALUES ('batch_legacy', 'generate', 'legacy', 'active', 'gpt-image-2.5-sunburst', ?, ?, 'x', 'x')",
            (str(self.temp / "legacy-out"), json.dumps(body)),
        )
        for sequence in (1, 2):
            connection.execute(
                "INSERT INTO batch_items (batch_id, sequence, body_json, idempotency_key, status, updated_at) "
                "VALUES ('batch_legacy', ?, '{\"_asx_inherit_template\": true}', ?, 'pending', 'x')",
                (sequence, f"asx-batch_legacy-{sequence:06d}"),
            )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_unowned_legacy_records_are_never_resubmitted_and_move_only_on_confirmation(self) -> None:
        legacy, digest = self.legacy_ledger()
        # Not read by any identity until claimed.
        for env in (self.a, self.b):
            self.assertEqual(self.cli(env, "task", "list")[1]["tasks"], [])
            self.assertEqual(self.cli(env, "task", "recover")[1]["tasks"], [])
            self.assertEqual(self.cli(env, "batch", "list")[1]["batches"], [])
        code, out = self.cli(self.a, "ledger", "status")
        self.assertEqual((out["unowned_ledger"]["cloud_tasks"], out["unowned_ledger"]["batches"]), (2, 1))
        self.assertEqual(out["unowned_ledger"]["never_submitted"], 1)
        code, out = self.cli({**self.a, "ASYNX_STATE_PATH": str(legacy)}, "task", "recover")
        self.assertEqual((code, out["error"]["code"]), (2, "ledger_owner_unknown"), out)
        code, out = self.cli(self.a, "ledger", "migrate")
        self.assertEqual((code, out["error"]["code"]), (2, "ownership_confirmation_required"), out)
        self.assertEqual(self.cli(self.a, "task", "list")[1]["tasks"], [])
        self.assertEqual(self.attempts(), [])

        code, out = self.cli(self.a, "ledger", "migrate", "--confirm-ownership")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["records"], {"cloud_tasks": 2, "batches": 1, "never_submitted": 1, "other_gateway": 0})
        self.assertEqual(hashlib.sha256(legacy.read_bytes()).hexdigest(), digest, "source ledger was modified")
        self.assertEqual(len(self.cli(self.a, "task", "list")[1]["tasks"]), 2)
        self.assertEqual(self.cli(self.b, "task", "list")[1]["tasks"], [])

        # A task that reached the gateway can be followed by its (now confirmed) owner.
        code, out = self.cli(self.a, "task", "status", "local_sent", "--refresh")
        self.assertEqual((code, out["task"]["status"]), (0, "running"), out)
        self.assertEqual(self.requests("GET", "/v1/tasks/task_legacy"), ["account-a"])
        # One that never did has no confirmation by this identity: not resubmitted, by recover or batch.
        code, out = self.cli(self.a, "task", "recover", "local_never_sent")
        self.assertEqual(code, 4, out)
        self.assertEqual(out["tasks"][0]["error"]["code"], "charge_consent_missing")
        code, out = self.cli(self.a, "batch", "poll", "batch_legacy")
        self.assertEqual(out["batch"]["counts"], {"failed": 2}, out)
        code, out = self.cli(self.a, "task", "recover")
        self.assertEqual(self.attempts(), [])

        # A ledger that already has records is not overwritten by a second migration.
        code, out = self.cli(self.a, "ledger", "migrate", "--confirm-ownership")
        self.assertEqual((code, out["error"]["code"]), (2, "ledger_not_empty"), out)

    # ---- gateway binding --------------------------------------------------------------------------

    def test_tasks_are_not_continued_on_another_gateway(self) -> None:
        self.gateway.default_submit_status = "running"
        code, out = self.generate(self.a, "sent to gateway one")
        sent = out["local_id"]
        unsent = self.failed_submission(self.a)
        other, other_url, _ = run_fake_gateway()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        self.site.asynx_config["baseUrl"] = other_url
        code, out = self.cli(self.a, "task", "recover", unsent)
        self.assertEqual(out["tasks"][0]["error"]["code"], "task_gateway_mismatch", out)
        for command in (("task", "status", sent, "--refresh"), ("task", "cancel", sent), ("wait", sent)):
            code, out = self.cli(self.a, *command)
            self.assertEqual(out["error"]["code"], "task_gateway_mismatch", (command, out))
        self.assertEqual(other.submit_attempts, [])
        self.assertEqual([path for _, path, _ in other.received_requests if path.startswith("/v1/tasks/")], [])

    # ---- concurrency ------------------------------------------------------------------------------

    def test_two_identities_racing_for_one_new_ledger(self) -> None:
        shared = {"ASYNX_STATE_PATH": str(self.temp / "race.db")}
        results: dict[str, tuple[int, dict[str, Any]]] = {}
        threads = [
            threading.Thread(target=lambda n=name, e=env: results.__setitem__(
                n, self.generate({**e, **shared}, f"request of {n}")))
            for name, env in (("a", self.a), ("b", self.b))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [name for name, (code, _) in results.items() if code == 0]
        self.assertEqual(len(winners), 1, results)
        (loser,) = set(results) - set(winners)
        self.assertEqual(results[loser][1]["error"]["code"], "ledger_owner_mismatch")
        self.assertEqual(self.attempts(), [(f"account-{winners[0]}", f"request of {winners[0]}", self.attempts()[0][2])])
        owner = sqlite3.connect(shared["ASYNX_STATE_PATH"]).execute("SELECT account_id FROM ledger_owner").fetchall()
        self.assertEqual(owner, [(f"account-{winners[0]}",)])

    # ---- identity resolution ----------------------------------------------------------------------

    def test_without_a_usable_identity_only_the_unbound_ledger_is_used(self) -> None:
        local_id = self.failed_submission(self.a)
        no_context = {k: v for k, v in self.a.items() if k != "PLAIERS_SKILL_CONTEXT"}
        no_context["PLAIERS_SKILL_CONTEXT"] = str(self.temp / "missing.json")
        broken = self.identity("broken", agent="agent-a", account="account-a", pin=False)
        (self.temp / "credentials-broken" / "bindings" / "agent-a.json").unlink()
        for env in (no_context, broken):
            code, out = self.cli(env, "task", "list")
            self.assertEqual((code, out["tasks"]), (0, []), out)
            code, out = self.cli(env, "task", "status", local_id)
            self.assertEqual(out["error"]["code"], "task_not_found")
            code, out = self.cli(env, "task", "recover")
            self.assertEqual(out["error"]["code"], "need_login", out)
        self.assertEqual(len(self.attempts()), 1)

    def test_session_keeps_its_account_when_the_binding_changes(self) -> None:
        from shared.asxlib import auth
        from shared.asxlib.errors import AsxError

        env = self.identity("switch", agent="agent-s", account="account-a", pin=False)
        with mock.patch.dict(os.environ, {"PLAIERS_SKILL_CONTEXT": env["PLAIERS_SKILL_CONTEXT"]}):
            os.environ.pop("ASYNX_PARTNER_TOKEN", None)
            os.environ.pop("ASYNX_BASE_URL", None)
            session = auth.load_auth_session()
            self.assertEqual(session.identity["account_id"], "account-a")
            self.assertEqual(auth.ledger_identity(), session.identity)
            pat = "sagein_pat_" + "b" * 64
            self.site.pats[pat] = "account-b"
            write_binding(self.temp / "credentials-switch", "agent-s", "account-b", pat)
            session.token = ""
            with self.assertRaises(AsxError) as raised:
                session.ensure_valid_token()
            self.assertEqual(raised.exception.details["reason_code"], "account_changed")


if __name__ == "__main__":
    unittest.main()
