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


def validate_api_key(value: str) -> str:
    key = value.strip()
    if not key:
        raise AsxError(
            "API Key 不能为空，请从 Asynx 控制台获取。",
            code="missing_api_key",
        )
    if not key.startswith("asx-"):
        raise AsxError(
            "API Key 格式不正确，应以 asx- 开头。",
            code="invalid_api_key",
        )
    return key


def load_credentials() -> tuple[str, str]:
    config = read_config()
    env_key = os.environ.get("ASYNX_API_KEY")
    key = env_key or config.get("api_key")
    if not key:
        raise AsxError(
            "未配置 Asynx API Key。请在终端执行 configure 命令保存密钥，"
            "或设置 ASYNX_API_KEY 环境变量。",
            code="not_configured",
            exit_code=2,
        )
    api_key = validate_api_key(key)
    env_url = os.environ.get("ASYNX_BASE_URL")
    configured_url = config.get("base_url")
    base_url = validate_base_url(env_url or configured_url or DEFAULT_BASE_URL)
    return api_key, base_url


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


def save_config(api_key: str, base_url: str) -> Path:
    payload = read_config()
    payload.update({"api_key": api_key, "base_url": base_url})
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


def _masked_key(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.startswith("asx-"):
        return f"asx-{normalized[4:8]}..." if len(normalized) > 8 else "asx-****"
    return "<invalid>"


def config_status() -> dict[str, Any]:
    config, source = read_config_with_source()
    env_key = os.environ.get("ASYNX_API_KEY")
    stored_key = config.get("api_key")
    key = env_key or stored_key
    credential_source = "environment" if env_key else "config_file" if stored_key else "none"
    base_url = os.environ.get("ASYNX_BASE_URL") or config.get("base_url") or DEFAULT_BASE_URL
    normalized_base_url: str | None = None
    base_url_valid = False
    if isinstance(base_url, str):
        try:
            normalized_base_url = validate_base_url(base_url)
            base_url_valid = True
        except AsxError:
            normalized_base_url = "<invalid>"
    overrides = [
        name
        for name in ("ASYNX_API_KEY", "ASYNX_BASE_URL", "ASYNX_CONFIG_PATH")
        if os.environ.get(name)
    ]
    canonical = config_path()
    return {
        "ok": True,
        "configured": isinstance(key, str) and bool(key.strip()),
        "credential_source": credential_source,
        "config_path": str(canonical),
        "loaded_config_path": str(source) if source else None,
        "config_exists": canonical.exists(),
        "api_key_prefix": _masked_key(key),
        "key_prefix_valid": isinstance(key, str) and key.strip().startswith("asx-"),
        "base_url": normalized_base_url,
        "base_url_valid": base_url_valid,
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


def doctor(*, verify: bool = False, modality: str | None = None) -> dict[str, Any]:
    status = config_status()
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    # 1. Config & credentials check
    config_ok = status["configured"] and status["key_prefix_valid"] and status["base_url_valid"]
    checks.append(
        {
            "name": "credentials",
            "ok": config_ok,
            "message": "API key and base URL configured"
            if config_ok
            else "API key or base URL missing or invalid",
        }
    )
    if not status["configured"]:
        issues.append(
            {
                "level": "error",
                "code": "not_configured",
                "message": "Asynx API key is not configured. Run configure command or set ASYNX_API_KEY.",
            }
        )
    elif not status["key_prefix_valid"]:
        issues.append(
            {
                "level": "error",
                "code": "invalid_api_key_format",
                "message": "API key format is invalid; expected prefix asx-",
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
        try:
            from .client import AsynxClient

            api_key, base_url = load_credentials()
            models, request_id = AsynxClient(base_url, api_key).models(modality=modality)
            result["verification"] = {
                "ok": True,
                "model_count": len(models),
                "request_id": request_id,
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


def configure(base_url_override: str | None = None) -> dict[str, Any]:
    if not sys.stdin.isatty():
        raise AsxError(
            "请在自己的终端运行配置命令并输入 API Key，不要在对话中发送密钥。",
            code="interactive_terminal_required",
        )
    migrated = migrate_legacy_config()
    current = read_config()
    configured_url = current.get("base_url") if isinstance(current.get("base_url"), str) else None
    base_url = validate_base_url(
        base_url_override or os.environ.get("ASYNX_BASE_URL") or configured_url or DEFAULT_BASE_URL
    )
    log(f"配置文件：{config_path()}")
    if migrated:
        log(f"已将旧配置保存到：{migrated}，原文件已保留。")
    if base_url != DEFAULT_BASE_URL:
        log(f"服务地址：{base_url}")
    if base_url == DEFAULT_BASE_URL:
        log("还没有 API Key？请前往 https://asynx.llmapi.site/api-keys 创建。")
    log("输入时不会显示字符，这是正常的。")
    existing_key = current.get("api_key") if isinstance(current.get("api_key"), str) else ""
    label = "请输入 API Key（直接回车保留已保存的密钥）：" if existing_key else "请输入 API Key："
    try:
        entered_key = getpass.getpass(label).strip()
    except (EOFError, KeyboardInterrupt):
        raise AsxError("已取消输入，未保存新密钥。", code="interrupted", exit_code=130) from None
    api_key = validate_api_key(entered_key or existing_key)
    path = save_config(api_key, base_url)
    log("API Key 已保存，尚未验证连接。")
    if os.environ.get("ASYNX_API_KEY"):
        log("注意：当前设置了 ASYNX_API_KEY 环境变量，运行时会优先使用它；如需使用刚保存的密钥，请先移除或更新该环境变量。")
    return {
        "ok": True,
        "configured": True,
        "base_url": base_url,
        "config_path": str(path.resolve()),
    }
