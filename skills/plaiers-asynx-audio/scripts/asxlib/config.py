from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .constants import DEFAULT_INITIAL_POLL_DELAY_SECONDS, VERSION
from .errors import AsxError
from .optional_deps import optional_runtime_status
from .output import log


def config_path() -> Path:
    """Return the stable, user-level configuration path."""
    override = os.environ.get("ASYNX_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        root = Path(app_data).expanduser() if app_data else Path.home() / "AppData" / "Roaming"
        return root / "Asynx" / "config.json"
    return Path.home() / ".config" / "asynx" / "config.json"


def legacy_config_paths() -> list[Path]:
    """Return discoverable paths used by releases that honored XDG_CONFIG_HOME."""
    if os.environ.get("ASYNX_CONFIG_PATH"):
        return []
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    if not xdg_home:
        return []
    legacy = Path(xdg_home).expanduser() / "asynx" / "config.json"
    canonical = config_path()
    return [] if legacy == canonical else [legacy]


def ledger_key(identity: dict[str, str]) -> str:
    """Directory name of one identity's ledger: site + Agent + account, nothing else."""
    canonical = json.dumps([identity["origin"], identity["agent_id"], identity["account_id"]], ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def unbound_state_path() -> Path:
    """Ledger used without an identity (local processing only). Releases before the per-identity split
    kept every task here, so it may hold cloud tasks nobody has claimed yet (see ``ledger migrate``)."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Asynx" / "state.db"
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "asx" / "state.db"


def state_path() -> Path:
    """Task ledger of the current identity.

    Cloud tasks, batches and charge confirmations are kept per site + Agent + account, so installations
    for different identities under one OS user never share them. ``ASYNX_STATE_PATH`` still names an
    explicit file; whichever file is opened, its recorded owner is checked in ``state.connect_db``.
    """
    override = os.environ.get("ASYNX_STATE_PATH")
    if override:
        return Path(override).expanduser()
    from .auth import ledger_identity

    identity = ledger_identity()
    unbound = unbound_state_path()
    candidate = unbound if identity is None else unbound.parent / "identities" / ledger_key(identity) / "state.db"
    try:
        candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if candidate.exists():
            with candidate.open("a"):
                pass
        else:
            test_file = candidate.parent / f".write_test_{os.getpid()}"
            test_file.touch()
            test_file.unlink()
        return candidate
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "asx"
        if identity is not None:
            fallback_dir = fallback_dir / "identities" / ledger_key(identity)
        fallback_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fallback_db = fallback_dir / "state.db"
        if candidate.exists() and not fallback_db.exists():
            try:
                shutil.copy2(candidate, fallback_db)
            except OSError:
                pass
        return fallback_db


def cache_path() -> Path:
    override = os.environ.get("ASYNX_CACHE_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Asynx" / "cache" / "models.json"
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    candidate = cache_home / "asx" / "models.json"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "asx" / "cache"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / "models.json"


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AsxError(
            f"无法读取配置文件，请检查文件权限和 JSON 格式：{path}",
            code="invalid_config",
            details={"config_path": str(path)},
        ) from exc
    if not isinstance(value, dict):
        raise AsxError(
            f"配置文件格式不正确，应为 JSON 对象：{path}",
            code="invalid_config",
            details={"config_path": str(path)},
        )
    return value


def read_config_with_source() -> tuple[dict[str, Any], Path | None]:
    canonical = config_path()
    if canonical.exists():
        return _read_config_file(canonical), canonical
    for path in legacy_config_paths():
        if path.exists():
            return _read_config_file(path), path
    return {}, None


def read_config() -> dict[str, Any]:
    return read_config_with_source()[0]


def initial_poll_delay_seconds() -> float:
    raw = os.environ.get("ASYNX_INITIAL_POLL_DELAY_SECONDS")
    value = raw if raw is not None else read_config().get(
        "initial_poll_delay_seconds", DEFAULT_INITIAL_POLL_DELAY_SECONDS
    )
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError
        delay = float(value)
        if not math.isfinite(delay) or not 0 <= delay <= 300:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise AsxError(
            "首次查询等待时间必须为 0–300 秒的数字，请检查 initial_poll_delay_seconds "
            "或 ASYNX_INITIAL_POLL_DELAY_SECONDS。",
            code="invalid_poll_delay",
        ) from exc
    return delay


def validate_base_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parts = urlsplit(candidate)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.query
        or parts.fragment
    ):
        raise AsxError(
            "Base URL 格式不正确，应形如 https://asynx.llmapi.site/api",
            code="invalid_base_url",
            details={"base_url": value},
        )
    return candidate


def load_credentials() -> tuple[str, str]:
    from .auth import load_auth_session

    session = load_auth_session()
    return session.ensure_valid_token(), session.base_url


def load_client() -> Any:
    from .auth import load_auth_session
    from .client import AsynxClient

    session = load_auth_session()
    return AsynxClient(session.base_url, session=session)


def _write_config(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    prefix = f".tmp-{path.name}-"
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(path.parent),
            prefix=prefix,
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        if os.name != "nt":
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(path)
        if os.name != "nt":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        if "temporary" in locals() and temporary.exists():
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise AsxError(
            f"无法保存配置，请检查目录写入权限和剩余磁盘空间：{path}",
            code="config_write_failed",
            details={"config_path": str(path)},
        ) from exc
    return path


def save_config(payload: dict[str, Any]) -> Path:
    """Merge non-credential settings into the user config. JWTs are never stored here."""
    for key in ("token", "asynx", "origin"):
        if key in payload:
            raise AsxError(
                f"配置文件不保存 {key}；JWT 只经凭据上下文中的桥接命令获取",
                code="invalid_config",
                details={"key": key},
            )
    merged = read_config()
    merged.update(payload)
    return _write_config(merged, config_path())


def migrate_legacy_config() -> Path | None:
    canonical = config_path()
    if canonical.exists() or os.environ.get("ASYNX_CONFIG_PATH"):
        return None
    for legacy in legacy_config_paths():
        if legacy.exists():
            _write_config(_read_config_file(legacy), canonical)
            return canonical
    return None


def config_status() -> dict[str, Any]:
    from .auth import legacy_credential_env, load_credential_context

    config, source = read_config_with_source()
    legacy_env = legacy_credential_env()

    context: dict[str, Any] = {"ok": False}
    try:
        loaded = load_credential_context()
        context = {
            "ok": True,
            "path": loaded["path"],
            "origin": loaded["origin"],
            "agent_id": loaded["agent_id"],
            "account_id": loaded["account_id"],
        }
    except AsxError as exc:
        context = {"ok": False, "path": exc.details.get("context_path"), "error": exc.payload()["error"]}

    # The gateway base URL comes from the site's asynx-config at use time; there is no other source.
    credential_source = "credential_context" if context["ok"] and not legacy_env else "none"

    overrides = [name for name in ("ASYNX_CONFIG_PATH", "PLAIERS_SKILL_CONTEXT") if os.environ.get(name)]
    canonical = config_path()
    return {
        "ok": True,
        "configured": credential_source != "none",
        "credential_source": credential_source,
        "credential_context": context,
        "config_path": str(canonical),
        "loaded_config_path": str(source) if source else None,
        "config_exists": canonical.exists(),
        "origin": context.get("origin"),
        "legacy_credential_env": legacy_env,
        "base_url_valid": True,
        "skill_version": VERSION,
        "environment_overrides": overrides,
    }


def legacy_installation_notice() -> dict[str, Any] | None:
    try:
        home = Path.home()
        candidates = [home / agent / "skills" / "asx" for agent in (".agents", ".claude", ".codex")]
        paths = [str(path) for path in candidates if path.exists() or path.is_symlink()]
    except (OSError, RuntimeError):
        return None
    if not paths:
        return None
    return {
        "code": "legacy_skill_installation",
        "paths": paths,
        "message": (
            "发现可能残留的旧版 asx 入口。用户要求安装或升级时，先确认这些路径属于 Asynx，"
            "等待旧版命令退出，将入口移到所有 Agent 的 skills 扫描目录之外备份，再继续安装。"
            "符号链接只移动链接本身；保留密钥、任务数据和图片。其他情况下仅提醒用户，不移动文件。"
        ),
    }


def _ffmpeg_install_hint() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macOS 上可运行：brew install ffmpeg"
    if system == "windows":
        return "Windows 上可运行：winget install Gyan.FFmpeg 或 choco install ffmpeg"
    return "Linux 上可运行：sudo apt-get install ffmpeg (Debian/Ubuntu) 或 sudo dnf install ffmpeg (Fedora)"


def check_media_tools() -> dict[str, Any]:
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    available = bool(ffmpeg_bin and ffprobe_bin)
    ffmpeg_version: str | None = None
    if ffmpeg_bin:
        try:
            output = subprocess.check_output(
                [ffmpeg_bin, "-version"], stderr=subprocess.STDOUT, timeout=5, text=True
            )
            first_line = output.splitlines()[0] if output.splitlines() else ""
            ffmpeg_version = first_line.strip()
        except Exception:
            ffmpeg_version = "unknown"

    return {
        "available": available,
        "ffmpeg": ffmpeg_bin,
        "ffprobe": ffprobe_bin,
        "version": ffmpeg_version,
        "install_hint": None if available else _ffmpeg_install_hint(),
    }


def check_state_ledger() -> dict[str, Any]:
    from .state import SCHEMA_VERSION, connect_db
    try:
        conn = connect_db()
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        conn.close()
        return {
            "ok": True,
            "path": str(state_path()),
            "schema_version": version,
            "expected_version": SCHEMA_VERSION,
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(state_path()),
            "error": str(exc),
        }


def doctor(
    *,
    verify: bool = False,
    modality: str | None = None,
) -> dict[str, Any]:
    status = config_status()
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    # 1. Config & credentials check
    config_ok = bool(status["configured"]) and status["base_url_valid"]
    checks.append(
        {
            "name": "credentials",
            "ok": config_ok,
            "message": "Plaiers credential context configured"
            if config_ok
            else "Plaiers credential context not configured",
        }
    )
    if status["legacy_credential_env"]:
        issues.append(
            {
                "level": "error",
                "code": "legacy_credentials_removed",
                "message": f"{' / '.join(status['legacy_credential_env'])} is no longer supported. Unset it and obtain "
                "the JWT through get-skill-jwt.mjs (CI: bind --origin/--agent-id/--account-id/--credential-file "
                "explicitly in the credential context).",
            }
        )
    elif not status["configured"]:
        issues.append(
            {
                "level": "error",
                "code": "need_login",
                "message": "Plaiers credential context not found or invalid. Install this skill through plaiers-community (search_skills / get_skill) with the skill_jwt scope.",
                "details": status["credential_context"].get("error"),
            }
        )
    elif not config_ok:
        issues.append(
            {
                "level": "error",
                "code": "invalid_base_url",
                "message": "Asynx gateway base URL is invalid.",
            }
        )

    # 2. State ledger check
    ledger = check_state_ledger()
    checks.append(
        {
            "name": "state_ledger",
            "ok": ledger["ok"],
            "message": f"SQLite state ledger healthy (schema v{ledger.get('schema_version', '?')})"
            if ledger["ok"]
            else f"SQLite state ledger check failed: {ledger.get('error')}",
        }
    )
    if not ledger["ok"]:
        issues.append(
            {
                "level": "error",
                "code": "state_ledger_error",
                "message": f"State ledger error: {ledger.get('error')}",
            }
        )

    # 3. Optional runtime (Pillow): not prepared is a normal state, never a failure
    optional_runtime = optional_runtime_status()
    checks.append(
        {
            "name": "optional_runtime",
            "ok": optional_runtime["status"] != "invalid",
            "status": optional_runtime["status"],
            "message": f"Shared runtime ready ({optional_runtime.get('requirement')})"
            if optional_runtime["status"] == "ready"
            else optional_runtime.get("message"),
        }
    )
    if optional_runtime["status"] == "invalid":
        issues.append(
            {
                "level": "warning",
                "code": "runtime_invalid",
                "message": optional_runtime.get("message"),
                "details": {"reason": optional_runtime.get("reason"), "path": optional_runtime.get("path")},
            }
        )

    # 4. Media tools (ffmpeg/ffprobe) check
    media_tools = check_media_tools()
    checks.append(
        {
            "name": "media_tools",
            "ok": media_tools["available"],
            "message": f"ffmpeg/ffprobe available ({media_tools.get('version')})"
            if media_tools["available"]
            else f"ffmpeg/ffprobe not found. {media_tools.get('install_hint')}",
        }
    )
    if not media_tools["available"] and modality in {"audio", "video"}:
        issues.append(
            {
                "level": "warning",
                "code": "ffmpeg_missing",
                "message": f"ffmpeg/ffprobe is required for advanced audio/video processing. {media_tools.get('install_hint')}",
            }
        )

    result: dict[str, Any] = {
        "ok": not any(issue["level"] == "error" for issue in issues),
        "skill_version": VERSION,
        "modality": modality,
        "python_executable": sys.executable,
        "home": str(Path.home()),
        "config": status,
        "ledger": ledger,
        "optional_runtime": optional_runtime,
        "media_tools": media_tools,
        "checks": checks,
        "issues": issues,
    }

    if verify:
        try:
            client = load_client()
            identity, _id_req = client.partner_identity()
            models, request_id = client.models(modality=modality)
            if not config_ok:
                status = config_status()
                result["config"] = status
                issues[:] = [i for i in issues if i.get("code") not in {"need_login", "invalid_base_url"}]
                for chk in checks:
                    if chk.get("name") == "credentials":
                        chk["ok"] = True
                        chk["message"] = "Plaiers credential context / partner token configured"
                result["ok"] = not any(issue["level"] == "error" for issue in issues)
            result["verification"] = {
                "ok": True,
                "model_count": len(models),
                "request_id": request_id,
                "partner_identity": identity,
            }
            checks.append(
                {
                    "name": "api_verification",
                    "ok": True,
                    "message": f"Asynx API verification succeeded ({len(models)} models available)",
                }
            )
        except AsxError as exc:
            result["ok"] = False
            result["verification"] = {"ok": False, "error": exc.payload()["error"]}
            issues.append(
                {
                    "level": "error",
                    "code": "api_verification_failed",
                    "message": exc.message,
                }
            )
            checks.append(
                {
                    "name": "api_verification",
                    "ok": False,
                    "message": f"Asynx API verification failed: {exc.message}",
                }
            )
        except Exception as exc:
            result["ok"] = False
            result["verification"] = {
                "ok": False,
                "error": {"code": "verification_failed", "message": f"API 验证失败：{exc}"},
            }
            issues.append(
                {
                    "level": "error",
                    "code": "api_verification_failed",
                    "message": str(exc),
                }
            )
            checks.append(
                {
                    "name": "api_verification",
                    "ok": False,
                    "message": f"Asynx API verification failed: {exc}",
                }
            )

    return result
