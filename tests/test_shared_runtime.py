"""Issue 04: one shared, hash-locked, on-demand Pillow runtime for all Asset Skills.

CLI calls run with ``python -S`` (no site-packages, so no interpreter Pillow) and
an isolated ``XDG_DATA_HOME``/``HOME``. ``runtime prepare`` installs a small
fixture wheel from a local directory (``ASYNX_RUNTIME_FIND_LINKS``) pinned by a
test lock (``ASYNX_RUNTIME_LOCK``); pip itself is the real one.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.asxlib import runtime_env  # noqa: E402
from tests.bridge_env import bridge_env  # noqa: E402

VERSION = "12.3.0"
FAKE_PIL = {
    "PIL/__init__.py": f'__version__ = "{VERSION}"\nUnidentifiedImageError = OSError\nFIXTURE = True\n',
    "PIL/Image.py": (
        "class _Image:\n    def close(self):\n        pass\n\n"
        "def new(mode, size, color=0):\n    return _Image()\n"
    ),
    "PIL/features.py": "def check(name):\n    return True\n",
}


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build_fixture_wheel(directory: Path, *, version: str = VERSION) -> Path:
    files = {name: body.encode() for name, body in FAKE_PIL.items()}
    dist = f"pillow-{version}.dist-info"
    files[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: pillow\nVersion: {version}\n".encode()
    files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: asset-skills-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    record = "".join(f"{name},{_record_hash(data)},{len(data)}\n" for name, data in files.items())
    files[f"{dist}/RECORD"] = (record + f"{dist}/RECORD,,\n").encode()
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"pillow-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return wheel


def write_lock(path: Path, wheels: list[tuple[str, str]], *, version: str = VERSION) -> Path:
    path.write_text(
        json.dumps({
            "schema": 1,
            "package": "pillow",
            "version": version,
            "requirement": f"pillow=={version}",
            "wheels": [{"filename": name, "sha256": digest} for name, digest in wheels],
        }),
        encoding="utf-8",
    )
    return path


class RuntimeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.build_skills import build_skill

        for modality in ("image", "audio", "video"):
            build_skill(modality)

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.wheels = self.temp / "wheels"
        wheel = build_fixture_wheel(self.wheels)
        self.wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        self.lock = write_lock(self.temp / "lock.json", [(wheel.name, self.wheel_sha)])
        self.data_home = self.temp / "data"
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.temp / "home"),
            "XDG_DATA_HOME": str(self.data_home),
            "PIP_CACHE_DIR": str(self.temp / "pip-cache"),
            "ASYNX_RUNTIME_LOCK": str(self.lock),
            "ASYNX_RUNTIME_FIND_LINKS": str(self.wheels),
            "ASYNX_STATE_PATH": str(self.temp / "state.db"),
            "ASYNX_CONFIG_PATH": str(self.temp / "config.json"),
            "ASYNX_UPDATE_CHECK": "off",
            # doctor needs a credential context; these tests never reach the gateway.
            **bridge_env(self, self.temp, "http://127.0.0.1:9/api"),
        }
        info = runtime_env.platform_info()
        self.runtime_dir = self.data_home / "plaiers" / "asynx-runtime" / info["key"]

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def script(self, skill: str) -> Path:
        return REPO_ROOT / "skills" / skill / "scripts" / "asynx.py"

    def run_cli(self, skill: str, *args: str, expect_ok: bool = True, **env: str) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-S", str(self.script(skill)), *args],
            capture_output=True, text=True, env={**self.env, **env}, cwd=self.temp,
        )
        payload = json.loads(proc.stdout)
        if expect_ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(payload["ok"], proc.stdout)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse(payload["ok"], proc.stdout)
        return payload

    def popen_prepare(self, skill: str, **env: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-S", str(self.script(skill)), "runtime", "prepare"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**self.env, **env},
        )

    def pillow_origin(self, skill: str) -> dict[str, Any]:
        """Import Pillow the way a skill does and report where it came from."""
        code = (
            "import json, sys; sys.path.insert(0, sys.argv[1]); "
            "from asxlib.optional_deps import import_pillow; from asxlib.errors import AsxError\n"
            "try:\n    (pil,) = import_pillow('', feature='test')\n"
            "    print(json.dumps({'ok': True, 'file': pil.__file__, 'fixture': getattr(pil, 'FIXTURE', False)}))\n"
            "except AsxError as exc:\n    print(json.dumps(exc.payload()))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-S", "-c", code, str(self.script(skill).parent)],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def leftovers(self) -> list[str]:
        base = self.runtime_dir.parent
        return sorted(p.name for p in base.iterdir() if p.name.startswith(".")) if base.is_dir() else []


class TestPrepareAndReuse(RuntimeCase):
    def test_first_prepare_reuse_and_sharing_between_skills(self) -> None:
        status = self.run_cli("plaiers-asynx-image-pro", "runtime", "status")["runtime"]
        self.assertEqual(status["status"], "not_prepared")
        self.assertFalse(self.runtime_dir.exists(), "status must not prepare anything")
        self.assertEqual(self.pillow_origin("plaiers-asynx-image-pro")["error"]["code"], "dependency_missing")

        first = self.run_cli("plaiers-asynx-image-pro", "runtime", "prepare")
        self.assertTrue(first["prepared"])
        self.assertEqual(first["runtime"]["status"], "ready")
        receipt = json.loads((self.runtime_dir / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["requirement"], f"pillow=={VERSION}")
        self.assertEqual(receipt["hashes"], [self.wheel_sha])
        self.assertEqual(receipt["python"], f"{sys.version_info.major}.{sys.version_info.minor}")
        self.assertIn("prepared_at", receipt)
        self.assertIn("PIL/__init__.py", receipt["files"])
        self.assertIn(f"--hash=sha256:{self.wheel_sha}", (self.runtime_dir / "requirements.txt").read_text())
        self.assertEqual(self.leftovers(), [], "no staging directories may remain")

        origin = self.pillow_origin("plaiers-asynx-image-pro")
        self.assertTrue(origin["fixture"])
        self.assertTrue(origin["file"].startswith(str(self.runtime_dir / "site")))

        # Another skill reuses it: nothing is downloaded (the wheel source is gone).
        shutil.rmtree(self.wheels)
        again = self.run_cli("plaiers-asynx-audio", "runtime", "prepare")
        self.assertFalse(again["prepared"])
        self.assertTrue(again["reused"])
        self.assertEqual(json.loads((self.runtime_dir / "receipt.json").read_text())["prepared_at"], receipt["prepared_at"])
        shared = self.run_cli("plaiers-asynx-video", "runtime", "status")["runtime"]
        self.assertEqual(shared["status"], "ready")
        self.assertEqual(shared["path"], str(self.runtime_dir))
        self.assertTrue(self.pillow_origin("plaiers-asynx-video")["fixture"])

        doctor = self.run_cli("plaiers-asynx-audio", "doctor")
        self.assertEqual(doctor["optional_runtime"]["status"], "ready")

        removed = self.run_cli("plaiers-asynx-image-pro", "runtime", "remove")
        self.assertTrue(removed["removed"])
        self.assertFalse(self.runtime_dir.exists())
        self.assertEqual(self.run_cli("plaiers-asynx-audio", "runtime", "status")["runtime"]["status"], "not_prepared")


class TestConcurrency(RuntimeCase):
    def hold_lock(self) -> Any:
        base = self.runtime_dir.parent
        base.mkdir(parents=True, exist_ok=True)
        handle = (base / f"{self.runtime_dir.name}.lock").open("a+b")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    @unittest.skipIf(os.name == "nt", "POSIX lock helper")
    def test_second_prepare_waits_for_lock_then_installs(self) -> None:
        handle = self.hold_lock()
        try:
            proc = self.popen_prepare("plaiers-asynx-image-pro")
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), "prepare must wait while another prepare holds the lock")
            self.assertFalse(self.runtime_dir.exists())
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertTrue(json.loads(out)["prepared"])

    @unittest.skipIf(os.name == "nt", "POSIX lock helper")
    def test_lock_wait_times_out_as_runtime_busy(self) -> None:
        handle = self.hold_lock()
        try:
            payload = self.run_cli(
                "plaiers-asynx-image-pro", "runtime", "prepare", expect_ok=False, ASYNX_RUNTIME_LOCK_TIMEOUT="0.5"
            )
        finally:
            handle.close()
        self.assertEqual(payload["error"]["code"], "runtime_busy")
        self.assertFalse(self.runtime_dir.exists())

    def test_two_concurrent_prepares_install_once(self) -> None:
        procs = [self.popen_prepare("plaiers-asynx-image-pro"), self.popen_prepare("plaiers-asynx-audio")]
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, out + err)
            results.append(json.loads(out))
        self.assertEqual(sorted(r["prepared"] for r in results), [False, True])
        self.assertEqual(sorted(r["reused"] for r in results), [False, True])


class TestRejections(RuntimeCase):
    def test_hash_mismatch_installs_nothing(self) -> None:
        write_lock(self.lock, [(f"pillow-{VERSION}-py3-none-any.whl", "0" * 64)])
        payload = self.run_cli("plaiers-asynx-image-pro", "runtime", "prepare", expect_ok=False)
        self.assertEqual(payload["error"]["code"], "runtime_hash_mismatch", payload)
        self.assertFalse(self.runtime_dir.exists())
        self.assertEqual(self.leftovers(), [])

    def test_incompatible_python_is_unsupported_without_pip(self) -> None:
        write_lock(self.lock, [(f"pillow-{VERSION}-cp99-cp99-macosx_11_0_arm64.whl", "1" * 64)])
        payload = self.run_cli("plaiers-asynx-image-pro", "runtime", "prepare", expect_ok=False)
        error = payload["error"]
        self.assertEqual(error["code"], "runtime_unsupported")
        self.assertEqual(error["details"]["platform"]["python"], f"{sys.version_info.major}.{sys.version_info.minor}")
        self.assertFalse(self.runtime_dir.parent.exists(), "nothing may be created for an unsupported platform")
        self.assertEqual(self.run_cli("plaiers-asynx-image-pro", "runtime", "status")["runtime"]["status"], "unsupported")
        missing = self.pillow_origin("plaiers-asynx-image-pro")["error"]
        self.assertEqual(missing["code"], "dependency_missing")
        self.assertEqual(missing["details"]["runtime_status"], "unsupported")
        self.assertNotIn("prepare_command", missing["details"])

    def test_corrupt_runtime_is_reported_never_used_and_replaced_whole(self) -> None:
        self.run_cli("plaiers-asynx-image-pro", "runtime", "prepare")
        (self.runtime_dir / "site" / "PIL" / "Image.py").write_text("raise SystemExit('tampered')\n")
        status = self.run_cli("plaiers-asynx-video", "runtime", "status")["runtime"]
        self.assertEqual(status["status"], "invalid")
        self.assertEqual(status["reason"], "files_modified")
        self.assertIn("PIL/Image.py", status["files"])
        refused = self.pillow_origin("plaiers-asynx-video")["error"]
        self.assertEqual(refused["code"], "runtime_invalid")
        doctor = self.run_cli("plaiers-asynx-video", "doctor")
        self.assertIn("runtime_invalid", [i["code"] for i in doctor["issues"]])

        repaired = self.run_cli("plaiers-asynx-video", "runtime", "prepare")
        self.assertTrue(repaired["prepared"])
        self.assertTrue(repaired["replaced_invalid"])
        self.assertEqual(repaired["runtime"]["status"], "ready")
        self.assertTrue(self.pillow_origin("plaiers-asynx-video")["fixture"])

    def test_receipt_for_other_lock_is_mismatch(self) -> None:
        self.run_cli("plaiers-asynx-image-pro", "runtime", "prepare")
        other = build_fixture_wheel(self.temp / "other", version="12.4.0")
        write_lock(self.lock, [(other.name, hashlib.sha256(other.read_bytes()).hexdigest())], version="12.4.0")
        status = self.run_cli("plaiers-asynx-image-pro", "runtime", "status")["runtime"]
        self.assertEqual((status["status"], status["reason"]), ("invalid", "lock_mismatch"))


class TestLockedWheelSelection(unittest.TestCase):
    """The committed lock covers the platforms the size table claims."""

    def info(self, os_name: str, arch: str, python: str, *, libc: str | None = None, ft: bool = False) -> dict[str, Any]:
        return {
            "key": f"{os_name}-{arch}-py{python}", "os": os_name, "arch": arch, "libc": libc,
            "python": python, "implementation": "cpython", "free_threaded": ft,
        }

    def test_supported_platforms_have_exactly_one_wheel(self) -> None:
        from shared.asxlib.runtime_lock import LOCK

        for python in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            for info in (
                self.info("macos", "arm64", python),
                self.info("macos", "x86_64", python),
                self.info("linux", "x86_64", python, libc="glibc"),
                self.info("linux", "arm64", python, libc="glibc"),
                self.info("linux", "x86_64", python, libc="musl"),
                self.info("linux", "arm64", python, libc="musl"),
                self.info("windows", "x86_64", python),
                self.info("windows", "arm64", python),
            ):
                with self.subTest(platform=info["key"], libc=info["libc"]):
                    self.assertEqual(len(runtime_env.matching_hashes(LOCK, info)), 1)

    def test_unsupported_combinations(self) -> None:
        from shared.asxlib.runtime_lock import LOCK

        for info in (
            self.info("macos", "arm64", "3.9"),
            self.info("macos", "arm64", "3.14", ft=True),
            self.info("windows", "x86", "3.12"),
            {**self.info("linux", "x86_64", "3.12", libc="glibc"), "implementation": "pypy"},
        ):
            with self.subTest(info=info):
                self.assertEqual(runtime_env.matching_hashes(LOCK, info), [])

    def test_skills_ship_no_vendored_packages(self) -> None:
        self.assertFalse((REPO_ROOT / "shared" / "vendor").exists())
        for skill in (REPO_ROOT / "skills").iterdir():
            if skill.is_dir():
                self.assertFalse((skill / "scripts" / "vendor").exists(), skill.name)


if __name__ == "__main__":
    unittest.main()
