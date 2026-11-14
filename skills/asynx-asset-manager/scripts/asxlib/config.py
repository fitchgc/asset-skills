from __future__ import annotations

import getpass
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

from .constants import DEFAULT_BASE_URL, DEFAULT_INITIAL_POLL_DELAY_SECONDS, VERSION
from .errors import AsxError
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


def state_path() -> Path:
    override = os.environ.get("ASYNX_STATE_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Asynx" / "state.db"
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    candidate = state_home / "asx" / "state.db"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
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
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_db = fallback_dir / "state.db"
        if candidate.exists() and not fallback_db.exists():
            try:
                import shutil
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


def load_credentials(*, allow_login_ui: bool = True) -> tuple[str, str]:
    from .auth import load_auth_session

    session = load_auth_session(allow_login_ui=allow_login_ui)
    return session.ensure_valid_token(), session.base_url


def load_client(*, allow_login_ui: bool = True) -> Any:
    from .auth import load_auth_session
    from .client import AsynxClient

    session = load_auth_session(allow_login_ui=allow_login_ui)
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


def save_config(payload_or_token: dict[str, Any] | str, base_url: str | None = None) -> Path:
    payload = read_config()
    if isinstance(payload_or_token, dict):
        payload.update(payload_or_token)
    else:
        payload["token"] = payload_or_token
        if base_url:
            asynx_cfg = dict(payload.get("asynx")) if isinstance(payload.get("asynx"), dict) else {}
            asynx_cfg["baseUrl"] = validate_base_url(base_url)
            payload["asynx"] = asynx_cfg
    return _write_config(payload, config_path())


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
    from .auth import decode_jwt, resolve_login_svr_url

    config, source = read_config_with_source()
    env_token = os.environ.get("ASYNX_PARTNER_TOKEN")
    stored_token = config.get("token") if isinstance(config.get("token"), str) else None
    stored_refresh = config.get("refreshToken") if isinstance(config.get("refreshToken"), str) else None
    token = env_token.strip() if isinstance(env_token, str) and env_token.strip() else stored_token

    credential_source = (
        "environment"
        if isinstance(env_token, str) and env_token.strip()
        else "session_file"
        if (stored_token or stored_refresh)
        else "none"
    )

    asynx_cfg = config.get("asynx") if isinstance(config.get("asynx"), dict) else {}
    raw_base_url = (
        os.environ.get("ASYNX_BASE_URL")
        or asynx_cfg.get("baseUrl")
        or config.get("base_url")
    )
    normalized_base_url: str | None = None
    base_url_valid = False
    if isinstance(raw_base_url, str) and raw_base_url.strip():
        try:
            normalized_base_url = validate_base_url(raw_base_url)
            base_url_valid = True
        except AsxError:
            normalized_base_url = "<invalid>"

    try:
        login_svr_url = resolve_login_svr_url(config)
    except AsxError:
        login_svr_url = "<invalid>"

    token_expires_at: int | None = None
    token_kid: str | None = None
    if isinstance(token, str) and token.strip():
        try:
            hdr, pld = decode_jwt(token.strip())
            if isinstance(hdr.get("kid"), str):
                token_kid = hdr["kid"]
            if pld.get("exp") is not None:
                token_expires_at = int(pld["exp"])
        except Exception:
            pass

    user = config.get("user") if isinstance(config.get("user"), dict) else None
    overrides = [
        name
        for name in (
            "ASYNX_LOGIN_SVR_URL",
            "ASYNX_PARTNER_TOKEN",
            "ASYNX_BASE_URL",
            "ASYNX_CONFIG_PATH",
        )
        if os.environ.get(name)
    ]
    canonical = config_path()
    configured = bool(
        (isinstance(env_token, str) and env_token.strip())
        or (isinstance(stored_token, str) and stored_token.strip())
        or (isinstance(stored_refresh, str) and stored_refresh.strip())
    )
    return {
        "ok": True,
        "configured": configured,
        "credential_source": credential_source,
        "config_path": str(canonical),
        "loaded_config_path": str(source) if source else None,
        "config_exists": canonical.exists(),
        "login_svr_url": login_svr_url,
        "has_token": bool(isinstance(token, str) and token.strip()),
        "has_refresh_token": bool(isinstance(stored_refresh, str) and stored_refresh.strip()),
        "token_expires_at": token_expires_at,
        "token_kid": token_kid,
        "base_url": normalized_base_url,
        "base_url_valid": base_url_valid,
        "asynx": asynx_cfg or None,
        "user": user,
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


def check_pillow() -> dict[str, Any]:
    try:
        import PIL
        from PIL import Image
        return {
            "available": True,
            "version": getattr(PIL, "__version__", "unknown"),
            "path": getattr(PIL, "__file__", None),
        }
    except ImportError:
        return {
            "available": False,
            "version": None,
            "install_hint": "本地图像处理依赖 Pillow，可运行 pip install pillow 安装",
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
    allow_login_ui: bool | None = None,
) -> dict[str, Any]:
    status = config_status()
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    # 1. Config & credentials check
    config_ok = bool(status["configured"]) and (
        status["credential_source"] == "session_file" or status["base_url_valid"]
    )
    checks.append(
        {
            "name": "credentials",
            "ok": config_ok,
            "message": "Partner token session configured"
            if config_ok
            else "Login session or partner token not configured",
        }
    )
    if not status["configured"]:
        issues.append(
            {
                "level": "error",
                "code": "need_login",
                "message": "Asynx login session is not configured. Run login/configure or set ASYNX_PARTNER_TOKEN.",
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

    # 3. Pillow check
    pil_info = check_pillow()
    checks.append(
        {
            "name": "pillow",
            "ok": pil_info["available"],
            "message": f"Pillow {pil_info.get('version')} available"
            if pil_info["available"]
            else pil_info.get("install_hint", "Pillow not available"),
        }
    )
    if not pil_info["available"] and modality == "image":
        issues.append(
            {
                "level": "warning",
                "code": "pillow_missing",
                "message": pil_info.get("install_hint"),
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
        "pillow": pil_info,
        "media_tools": media_tools,
        "checks": checks,
        "issues": issues,
    }

    if verify:
        from .auth import is_login_ui_allowed

        effective_allow_login = is_login_ui_allowed() if allow_login_ui is None else allow_login_ui
        try:
            client = load_client(allow_login_ui=effective_allow_login)
            identity, _id_req = client.partner_identity()
            models, request_id = client.models(modality=modality)
            if not config_ok:
                status = config_status()
                result["config"] = status
                issues[:] = [i for i in issues if i.get("code") not in {"need_login", "invalid_base_url"}]
                for chk in checks:
                    if chk.get("name") == "credentials":
                        chk["ok"] = True
                        chk["message"] = "Partner token session configured"
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


def configure(
    base_url_override: str | None = None,
    *,
    login_svr_url_override: str | None = None,
) -> dict[str, Any]:
    from .auth import login_and_provision, resolve_login_svr_url, validate_login_svr_url

    if not sys.stdin.isatty():
        raise AsxError(
            "请在自己的终端运行登录配置命令，或使用交互式登录工作台。",
            code="interactive_terminal_required",
        )
    migrated = migrate_legacy_config()
    current = read_config()
    default_login_url = validate_login_svr_url(
        login_svr_url_override or resolve_login_svr_url(current)
    )
    if base_url_override:
        os.environ["ASYNX_BASE_URL"] = validate_base_url(base_url_override)
    log(f"配置文件：{config_path()}")
    if migrated:
        log(f"已将旧配置保存到：{migrated}，原文件已保留。")
    try:
        entered_url = input(f"Identity Service 地址 [{default_login_url}]：").strip()
        login_svr_url = validate_login_svr_url(entered_url or default_login_url)
        username = input("用户名：").strip()
        password = getpass.getpass("密码：")
    except (EOFError, KeyboardInterrupt):
        raise AsxError("已取消登录配置。", code="interrupted", exit_code=130) from None

    session, identity = login_and_provision(username, password, login_svr_url=login_svr_url)
    log("登录成功，已保存会话并完成 Partner Identity 开通。")
    if os.environ.get("ASYNX_PARTNER_TOKEN"):
        log("注意：当前设置了 ASYNX_PARTNER_TOKEN 环境变量，运行时会优先使用它。")
    return {
        "ok": True,
        "configured": True,
        "login_svr_url": session.login_svr_url,
        "base_url": session.base_url,
        "user": session.user,
        "partner_identity": identity,
        "config_path": str(config_path().resolve()),
    }
