"""Workbench side of the shared Pillow runtime: re-check after a late prepare, and the consented prepare endpoint."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))

from shared.asxlib import runtime_env  # noqa: E402
from shared.asxlib.ui_server import WorkbenchServer  # noqa: E402


class TestActivateRechecks(unittest.TestCase):
    def setUp(self) -> None:
        runtime_env._activated = None
        self.addCleanup(setattr, runtime_env, "_activated", None)

    def test_not_prepared_is_not_cached_ready_is(self) -> None:
        site = tempfile.mkdtemp()
        self.addCleanup(lambda: site in sys.path and sys.path.remove(site))
        states = [{"status": "not_prepared"}, {"status": "ready", "site": site}]
        with mock.patch.object(runtime_env, "inspect_runtime", side_effect=states) as inspect:
            self.assertEqual(runtime_env.activate()["status"], "not_prepared")
            self.assertEqual(runtime_env.activate()["status"], "ready", "prepared later in the same process")
            self.assertEqual(runtime_env.activate()["status"], "ready")
        self.assertEqual(inspect.call_count, 2, "a ready runtime is checked once")
        self.assertIn(site, sys.path)


class _EndpointCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = WorkbenchServer(
            mode="tools", source_path=None, output_path=None, output_dir=tempfile.mkdtemp(),
            static_dir=REPO_ROOT / "src" / "ui" / "image",
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_address[1]}/api/runtime/prepare",
            data=json.dumps(body).encode(), method="POST",
            headers={"X-Asx-Session-Token": self.server.session_token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


class TestPrepareEndpoint(_EndpointCase):
    def test_refuses_without_the_page_confirmation(self) -> None:
        with mock.patch.object(runtime_env, "prepare_runtime") as prepare:
            status, data = self.post({})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"]["code"], "confirmation_required")
        prepare.assert_not_called()

    def test_prepares_after_confirmation(self) -> None:
        result = {"ok": True, "prepared": True, "reused": False, "runtime": {"status": "ready"}}
        with mock.patch.object(runtime_env, "prepare_runtime", return_value=result) as prepare:
            status, data = self.post({"user_confirmed": True})
        self.assertEqual(status, 200)
        self.assertEqual(data, result)
        prepare.assert_called_once_with()


class TestPrepareEndpointEnvironmentFailures(_EndpointCase):
    """Real prepare_runtime() behind the endpoint; pip install itself is never run."""

    def setUp(self) -> None:
        super().setUp()
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, True)
        lock = self.temp / "lock.json"
        lock.write_text(json.dumps({
            "schema": 1, "package": "pillow", "version": "12.3.0", "requirement": "pillow==12.3.0",
            "wheels": [{"filename": "pillow-12.3.0-py3-none-any.whl", "sha256": "0" * 64}],
        }), encoding="utf-8")
        self.data_home = self.temp / "data"
        env = mock.patch.dict("os.environ", {"ASYNX_RUNTIME_LOCK": str(lock), "XDG_DATA_HOME": str(self.data_home)})
        env.start()
        self.addCleanup(env.stop)
        pip = mock.patch.object(runtime_env, "_pip_available", return_value=True)
        pip.start()
        self.addCleanup(pip.stop)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores directory permissions")
    def test_unwritable_runtime_dir_returns_json_error(self) -> None:
        self.data_home.mkdir()
        self.data_home.chmod(0o500)
        self.addCleanup(self.data_home.chmod, 0o700)
        with mock.patch.object(runtime_env.subprocess, "run") as run:
            status, data = self.post({"user_confirmed": True})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "runtime_io_error")
        self.assertIn("运行环境目录无法创建或写入", data["error"]["message"])
        self.assertEqual(data["error"]["details"], {"step": "filesystem", "errno": 13, "reason": "Permission denied"})
        body = json.dumps(data, ensure_ascii=False)
        for private in (str(self.temp), str(self.temp.resolve()), str(Path.home())):
            self.assertNotIn(private, body, "no local paths in the response")
        self.assertNotIn("Traceback", body)
        run.assert_not_called()

    def test_pip_timeout_returns_json_error_and_leaves_no_staging(self) -> None:
        def slow_pip(command, **kwargs):
            raise runtime_env.subprocess.TimeoutExpired(command, kwargs["timeout"])

        with mock.patch.object(runtime_env.subprocess, "run", side_effect=slow_pip) as run:
            status, data = self.post({"user_confirmed": True})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "runtime_prepare_timeout")
        self.assertEqual(data["error"]["details"], {"step": "pip_install", "timeout_seconds": 900})
        self.assertEqual(run.call_args.args[0][1:4], ["-m", "pip", "install"])
        base = runtime_env.runtime_base()
        self.assertEqual([p.name for p in base.iterdir() if "staging" in p.name], [], "staging removed")
        self.assertFalse(runtime_env.runtime_dir().exists(), "no runtime enabled")


if __name__ == "__main__":
    unittest.main()
