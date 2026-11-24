"""Shared, isolated, hash-locked Pillow runtime for every Asset Skill.

One runtime per ``(os, arch, python major.minor)`` lives under
``${XDG_DATA_HOME:-~/.local/share}/plaiers/asynx-runtime/<os>-<arch>-py<maj.min>/``
(Windows: ``%LOCALAPPDATA%\\plaiers\\asynx-runtime\\…``)::

    site/            pip --target directory, added to sys.path only after verification
    requirements.txt the exact hash-locked requirement that was installed
    receipt.json     {requirement, hashes, python, platform, prepared_at, files}

It is created only by ``asynx.py runtime prepare``. The system Python, user
site-packages and pip configuration are never modified. A runtime whose receipt
or files do not verify is reported as ``invalid`` and never put on ``sys.path``.

Test/offline overrides: ``ASYNX_RUNTIME_LOCK`` (JSON lock file),
``ASYNX_RUNTIME_FIND_LINKS`` (local wheel directory, implies ``--no-index``),
``ASYNX_RUNTIME_LOCK_TIMEOUT`` (seconds to wait for a concurrent prepare).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import sysconfig
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import AsxError

RECEIPT_SCHEMA = 1
_SKIPPED_DIRS = {"__pycache__"}
_activated: dict[str, Any] | None = None


# ---------------------------------------------------------------- platform & lock


def load_lock() -> dict[str, Any]:
    override = os.environ.get("ASYNX_RUNTIME_LOCK")
    if override:
        try:
            return json.loads(Path(override).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AsxError(f"运行环境锁文件无法读取：{override}", code="runtime_lock_invalid") from exc
    from .runtime_lock import LOCK

    return LOCK


def platform_info() -> dict[str, Any]:
    machine = platform.machine().lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64"}.get(
        machine, machine or "unknown"
    )
    if sys.platform == "darwin":
        os_name = "macos"
    elif sys.platform.startswith("linux"):
        os_name = "linux"
    elif sys.platform in {"win32", "cygwin"}:
        os_name = "windows"
    else:
        os_name = sys.platform
    libc = None
    if os_name == "linux":
        libc_name = platform.libc_ver()[0]
        libc = "glibc" if libc_name == "glibc" else "musl"
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    return {
        "key": f"{os_name}-{arch}-py{python}",
        "os": os_name,
        "arch": arch,
        "libc": libc,
        "python": python,
        "implementation": sys.implementation.name,
        "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    }


def _platform_tag_matches(tag: str, info: dict[str, Any]) -> bool:
    if tag == "any":
        return True
    arch = info["arch"]
    if info["os"] == "macos":
        return tag.startswith("macosx_") and tag.endswith((f"_{arch}", "_universal2"))
    if info["os"] == "linux":
        linux_arch = "aarch64" if arch == "arm64" else arch
        prefix = "manylinux" if info["libc"] == "glibc" else "musllinux"
        return tag.startswith(prefix) and tag.endswith(f"_{linux_arch}")
    if info["os"] == "windows":
        return tag == {"x86_64": "win_amd64", "arm64": "win_arm64"}.get(arch)
    return False


def wheel_matches(filename: str, info: dict[str, Any]) -> bool:
    parts = filename.removesuffix(".whl").split("-")
    if len(parts) < 5:
        return False
    python_tags, abi_tags, platform_tags = (p.split(".") for p in parts[-3:])
    version = info["python"].replace(".", "")
    cp = f"cp{version}"
    python_ok = any(
        tag in {"py3", f"py{version}"} or (tag == cp and info["implementation"] == "cpython")
        for tag in python_tags
    )
    abi_ok = any(
        tag in {"none", "abi3"} or tag == (f"{cp}t" if info["free_threaded"] else cp) for tag in abi_tags
    )
    return python_ok and abi_ok and any(_platform_tag_matches(tag, info) for tag in platform_tags)


def matching_hashes(lock: dict[str, Any], info: dict[str, Any]) -> list[str]:
    return sorted({w["sha256"] for w in lock.get("wheels", []) if wheel_matches(w["filename"], info)})


def runtime_base() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"])
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "plaiers" / "asynx-runtime"


def runtime_dir(info: dict[str, Any] | None = None) -> Path:
    return runtime_base() / (info or platform_info())["key"]


# ---------------------------------------------------------------- inspection


def _file_manifest(site: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(site.rglob("*")):
        relative = path.relative_to(site)
        if _SKIPPED_DIRS.intersection(relative.parts):
            continue
        if path.is_symlink():
            files[relative.as_posix()] = "symlink"
        elif path.is_file():
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _unsupported(info: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "unsupported",
        "reason": "no_locked_wheel",
        "platform": info,
        "requirement": lock.get("requirement"),
        "message": f"锁定的 {lock.get('requirement')} 没有适用于 {info['key']}"
        f"（{info['implementation']}{'，free-threaded' if info['free_threaded'] else ''}）的预编译包",
    }


def inspect_runtime() -> dict[str, Any]:
    """Status without side effects: ready | not_prepared | invalid | unsupported."""
    lock = load_lock()
    info = platform_info()
    hashes = matching_hashes(lock, info)
    target = runtime_dir(info)
    base = {"path": str(target), "requirement": lock.get("requirement"), "platform": info}
    if not hashes:
        return {**base, **_unsupported(info, lock)}
    if not target.exists():
        return {**base, "status": "not_prepared"}

    def invalid(reason: str, **extra: Any) -> dict[str, Any]:
        return {**base, "status": "invalid", "reason": reason, **extra}

    try:
        receipt = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return invalid("receipt_missing")
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        return invalid("receipt_invalid")
    if receipt.get("requirement") != lock.get("requirement") or sorted(receipt.get("hashes") or []) != hashes:
        return invalid("lock_mismatch", receipt_requirement=receipt.get("requirement"))
    if receipt.get("python") != info["python"] or (receipt.get("platform") or {}).get("key") != info["key"]:
        return invalid("platform_mismatch", receipt_platform=receipt.get("platform"))
    expected = receipt.get("files")
    site = target / "site"
    if not isinstance(expected, dict) or not expected or not site.is_dir():
        return invalid("files_missing")
    actual = _file_manifest(site)
    if actual != expected:
        changed = sorted(
            name for name in set(actual) | set(expected) if actual.get(name) != expected.get(name)
        )
        return invalid("files_modified", files=changed[:10], files_total=len(changed))
    return {
        **base,
        "status": "ready",
        "site": str(site),
        "prepared_at": receipt.get("prepared_at"),
        "file_count": len(actual),
    }


def activate() -> dict[str, Any]:
    """Put a verified runtime on ``sys.path``; never a partial one.

    Only a ready runtime is cached: a long-running workbench re-checks on the next call, so a
    runtime prepared after start-up is picked up without a restart.
    """
    global _activated
    if _activated is not None:
        return _activated
    state = inspect_runtime()
    if state["status"] == "ready":
        if state["site"] not in sys.path:
            sys.path.insert(0, state["site"])
            importlib.invalidate_caches()
        _activated = state
    return state


# ---------------------------------------------------------------- prepare / remove


@contextmanager
def _wait_lock(path: Path) -> Iterator[None]:
    """Exclusive lock; a second prepare/remove waits for the first, then re-checks."""
    timeout = float(os.environ.get("ASYNX_RUNTIME_LOCK_TIMEOUT") or 900)
    deadline = time.monotonic() + timeout
    with path.open("a+b") as handle:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AsxError(
                        "另一个运行环境准备任务仍在进行，请稍后重试",
                        code="runtime_busy",
                        details={"lock": str(path), "waited_seconds": timeout},
                    ) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)


PIP_INSTALL_TIMEOUT = 900
SMOKE_TEST_TIMEOUT = 60


def _pip_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "--version"], capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def _smoke_test(site: Path, version: str) -> str | None:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import PIL; from PIL import Image, features; "
        "assert PIL.__version__ == sys.argv[2], PIL.__version__; assert features.check('webp'); "
        "Image.new('RGB', (1, 1)).close()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code, str(site), version],
            capture_output=True,
            text=True,
            check=False,
            timeout=SMOKE_TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise AsxError(
            f"运行环境自检超过 {SMOKE_TEST_TIMEOUT} 秒仍未完成，未启用",
            code="runtime_prepare_timeout",
            exit_code=2,
            details={"step": "smoke_test", "timeout_seconds": SMOKE_TEST_TIMEOUT},
        ) from exc
    return None if result.returncode == 0 else (result.stderr or result.stdout)[-2000:]


def prepare_runtime() -> dict[str, Any]:
    """Prepare the shared runtime; expected environment failures surface as ``AsxError``.

    Filesystem failures (e.g. a runtime directory that cannot be created or written) and pip
    timeouts are reported with a stable code, so the CLI and the workbench endpoint both return a
    structured error instead of an uncaught exception. Programming errors still propagate.
    """
    try:
        return _prepare_runtime()
    except OSError as exc:
        # Reason only: the response goes to the browser, so no local paths, traceback or environment.
        reason = exc.strerror or type(exc).__name__
        hint = "运行环境目录无法创建或写入" if isinstance(exc, PermissionError) else "运行环境准备时系统操作失败"
        raise AsxError(
            f"{hint}（{reason}），未启用新的运行环境。请检查 XDG_DATA_HOME（未设置时为 ~/.local/share）下 plaiers 目录的写入权限",
            code="runtime_io_error",
            exit_code=2,
            details={"step": "filesystem", "errno": exc.errno, "reason": reason},
        ) from exc


def _prepare_runtime() -> dict[str, Any]:
    lock = load_lock()
    info = platform_info()
    hashes = matching_hashes(lock, info)
    if not hashes:
        state = _unsupported(info, lock)
        raise AsxError(state["message"], code="runtime_unsupported", exit_code=2, details=state)
    if not _pip_available():
        raise AsxError(
            "当前 Python 没有可用的 pip，无法准备运行环境",
            code="runtime_unsupported",
            exit_code=2,
            details={"reason": "pip_missing", "python": sys.executable, "platform": info},
        )
    base = runtime_base()
    base.mkdir(parents=True, exist_ok=True)
    target = runtime_dir(info)
    with _wait_lock(base / f"{info['key']}.lock"):
        current = inspect_runtime()
        if current["status"] == "ready":
            return {"ok": True, "prepared": False, "reused": True, "runtime": current}
        stage = base / f".{info['key']}.staging-{os.getpid()}-{secrets.token_hex(4)}"
        try:
            site = stage / "site"
            site.mkdir(parents=True)
            requirement = lock["requirement"] + "".join(f" --hash=sha256:{h}" for h in hashes)
            (stage / "requirements.txt").write_text(requirement + "\n", encoding="utf-8")
            command = [
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input", "--no-compile", "--no-deps",
                "--require-hashes", "--only-binary=:all:",
                "--target", str(site), "-r", str(stage / "requirements.txt"),
            ]
            find_links = os.environ.get("ASYNX_RUNTIME_FIND_LINKS")
            if find_links:
                command += ["--no-index", "--find-links", find_links]
            env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_REQUIRE_VIRTUALENV": "0"}
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, check=False, env=env, timeout=PIP_INSTALL_TIMEOUT
                )
            except subprocess.TimeoutExpired as exc:
                raise AsxError(
                    f"下载或安装 Pillow 超过 {PIP_INSTALL_TIMEOUT} 秒仍未完成，已停止，未启用新的运行环境",
                    code="runtime_prepare_timeout",
                    exit_code=2,
                    details={"step": "pip_install", "timeout_seconds": PIP_INSTALL_TIMEOUT},
                ) from exc
            if result.returncode:
                output = (result.stderr or "") + (result.stdout or "")
                mismatch = "DO NOT MATCH THE HASHES" in output.upper()
                raise AsxError(
                    "下载的包与锁定哈希不一致，已拒绝安装" if mismatch else "运行环境准备失败，未做任何改动",
                    code="runtime_hash_mismatch" if mismatch else "runtime_prepare_failed",
                    exit_code=2,
                    details={"returncode": result.returncode, "pip_output": output[-2000:]},
                )
            problem = _smoke_test(site, lock["version"])
            if problem:
                raise AsxError(
                    "运行环境安装后自检失败，未启用", code="runtime_prepare_failed", exit_code=2,
                    details={"smoke_test": problem},
                )
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "requirement": lock["requirement"],
                "hashes": hashes,
                "python": info["python"],
                "implementation": info["implementation"],
                "platform": info,
                "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "files": _file_manifest(site),
            }
            (stage / "receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            replaced = None
            if target.exists():
                # An invalid runtime is swapped out whole, never patched in place.
                replaced = base / f".{info['key']}.invalid-{secrets.token_hex(4)}"
                os.replace(target, replaced)
            os.replace(stage, target)
            if replaced is not None:
                shutil.rmtree(replaced, ignore_errors=True)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
        state = inspect_runtime()
        return {
            "ok": True,
            "prepared": True,
            "reused": False,
            "replaced_invalid": current["status"] == "invalid",
            "runtime": state,
        }


def remove_runtime() -> dict[str, Any]:
    info = platform_info()
    base = runtime_base()
    target = runtime_dir(info)
    if not target.exists():
        return {"ok": True, "removed": False, "path": str(target)}
    with _wait_lock(base / f"{info['key']}.lock"):
        trash = base / f".{info['key']}.removing-{secrets.token_hex(4)}"
        os.replace(target, trash)
        shutil.rmtree(trash, ignore_errors=True)
    return {"ok": True, "removed": True, "path": str(target)}
