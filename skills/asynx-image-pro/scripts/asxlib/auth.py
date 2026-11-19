"""Short-lived JWT acquisition through the Plaiers community bridge (see docs/agent-jwt-contract.md).

The Asset Skills never read the ``sagein_pat_...`` credential and keep no JWT cache of their own.
Every JWT comes from running the exact ``get-skill-jwt.mjs`` argv that the Plaiers installer recorded
in the local credential context (``.plaiers-context.json`` next to the installed package). The bridge
keys its cache by site + Agent + account + PAT and checks the JWT ``sub`` against the bound account.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .constants import (
    DEFAULT_BASE_URL,
    DEFAULT_TOKEN_HEADER,
    MAX_API_RESPONSE_BYTES,
    PROACTIVE_REFRESH_LEEWAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    VERSION,
)
from .errors import AsxError

CONTEXT_FILENAME = ".plaiers-context.json"
CONTEXT_ENV = "PLAIERS_SKILL_CONTEXT"
CONTEXT_SCHEMA_VERSION = 1
BRIDGE_TIMEOUT_SECONDS = 60
# Flags accepted by get-skill-jwt.mjs (sagein-credentials.mjs options()); each takes one value.
_BRIDGE_FLAGS = frozenset({"--agent-id", "--origin", "--config-dir", "--credential-file", "--account-id"})
_SECRET_PATTERN = re.compile(r"(?:sagein|cg)_pat_[A-Za-z0-9]+|[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
INSTALL_HINT = (
    "请通过 plaiers-community 用 search_skills / get_skill 安装本 Skill（安装器会写入 "
    f"{CONTEXT_FILENAME}），并确认授权时勾选了 skill_jwt 权限。"
)


def _redact(text: str) -> str:
    return _SECRET_PATTERN.sub("[redacted]", text)


def validate_origin(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AsxError("缺少 Plaiers 站点 origin", code="invalid_origin", details={"origin": value})
    candidate = value.strip().rstrip("/")
    parts = urlsplit(candidate)
    local = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not parts.netloc
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or not (parts.scheme == "https" or (parts.scheme == "http" and local))
    ):
        raise AsxError(
            "Plaiers origin 格式不正确，应为 HTTPS 站点根地址（本地测试可用 http://localhost）",
            code="invalid_origin",
            details={"origin": value},
        )
    return candidate


def context_path() -> Path:
    override = os.environ.get(CONTEXT_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    # <skill>/scripts/asxlib/auth.py -> <skill>/.plaiers-context.json
    return Path(__file__).resolve().parents[2] / CONTEXT_FILENAME


def _invalid_context(path: Path, reason: str) -> AsxError:
    return AsxError(
        f"本机凭据上下文无效（{reason}）。{INSTALL_HINT}",
        code="need_login",
        exit_code=2,
        details={"reason_code": "invalid_context", "context_path": str(path), "reason": reason},
    )


def load_credential_context() -> dict[str, Any]:
    """Read and validate the installer-written context. It holds the bridge argv, never the PAT."""
    path = context_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AsxError(
            f"未找到本机凭据上下文 {path}。{INSTALL_HINT}",
            code="need_login",
            exit_code=2,
            details={"reason_code": "missing_context", "context_path": str(path)},
        ) from None
    except OSError as exc:
        raise _invalid_context(path, f"无法读取：{exc.strerror or exc}") from exc
    if re.search(r"(?:sagein|cg)_pat_", raw):
        raise _invalid_context(path, "包含访问令牌，上下文只能保存凭据命令")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _invalid_context(path, "不是合法 JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != CONTEXT_SCHEMA_VERSION:
        raise _invalid_context(path, f"schema_version 应为 {CONTEXT_SCHEMA_VERSION}")
    try:
        origin = validate_origin(data.get("origin"))
    except AsxError as exc:
        raise _invalid_context(path, "origin 无效") from exc
    argv = data.get("jwt_command")
    if not isinstance(argv, list) or len(argv) < 2 or not all(isinstance(a, str) and a for a in argv):
        raise _invalid_context(path, "jwt_command 应为非空字符串数组")
    flags: dict[str, str] = {}
    rest = argv[2:]
    if len(rest) % 2:
        raise _invalid_context(path, "jwt_command 参数不成对")
    for key, value in zip(rest[::2], rest[1::2]):
        if key not in _BRIDGE_FLAGS or key in flags or value.startswith("--"):
            raise _invalid_context(path, f"jwt_command 含不支持的参数 {key}")
        flags[key] = value
    if flags.get("--origin", "").rstrip("/") != origin:
        raise _invalid_context(path, "jwt_command 的 --origin 与 origin 不一致")
    if not flags.get("--agent-id"):
        raise _invalid_context(path, "jwt_command 缺少 --agent-id")
    if "agent_id" in data and data["agent_id"] != flags["--agent-id"]:
        raise _invalid_context(path, "agent_id 与 jwt_command 不一致")
    return {
        "path": str(path),
        "origin": origin,
        "agent_id": flags["--agent-id"],
        "account_id": flags.get("--account-id"),
        "jwt_command": list(argv),
    }


def run_jwt_bridge(argv: list[str]) -> str:
    """Run the recorded bridge argv without a shell; stdout carries only the JWT."""
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BRIDGE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AsxError(
            f"无法启动凭据桥接命令（{argv[0]} 不存在）。{INSTALL_HINT}",
            code="need_login",
            exit_code=2,
            details={"reason_code": "bridge_unavailable", "executable": argv[0]},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AsxError(
            "凭据桥接命令超时，请检查网络后重试。",
            code="need_login",
            exit_code=2,
            details={"reason_code": "bridge_timeout", "timeout_seconds": BRIDGE_TIMEOUT_SECONDS},
        ) from exc
    if proc.returncode != 0:
        message = _redact((proc.stderr or "").strip())[-500:]
        if "skill_jwt" in message:
            raise AsxError(
                "当前 Plaiers 授权缺少 skill_jwt 权限，请让用户重新授权并勾选该权限。",
                code="scope_missing",
                exit_code=2,
                details={"required_scope": "skill_jwt", "bridge_error": message},
            )
        raise AsxError(
            f"凭据桥接命令失败：{message or f'exit {proc.returncode}'}",
            code="need_login",
            exit_code=2,
            details={"reason_code": "bridge_failed", "exit_code": proc.returncode, "bridge_error": message},
        )
    token = (proc.stdout or "").strip()
    if token.count(".") != 2 or any(ch.isspace() for ch in token):
        raise AsxError("凭据桥接命令没有返回 JWT", code="invalid_sagein_response", exit_code=2)
    return token


def decode_jwt_segment(segment: str) -> dict[str, Any]:
    padding = "=" * (-len(segment) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(segment + padding).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("JWT segment is not a JSON object")
    return decoded


def decode_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts[:2]):
        raise ValueError("Malformed JWT")
    return decode_jwt_segment(parts[0]), decode_jwt_segment(parts[1])


def jwt_problem(
    token: str | None,
    asynx_config: dict[str, Any] | None,
    *,
    now: float | None = None,
    leeway_seconds: int = PROACTIVE_REFRESH_LEEWAY_SECONDS,
) -> str | None:
    """Return why the JWT cannot be used with this gateway config, or None if it can."""
    if not isinstance(token, str) or not token.strip():
        return "missing"
    if not isinstance(asynx_config, dict) or not asynx_config.get("baseUrl"):
        return "gateway_config_missing"
    try:
        header, payload = decode_jwt(token.strip())
        if asynx_config.get("kid") and header.get("kid") != asynx_config.get("kid"):
            return "kid_mismatch"
        if asynx_config.get("issuer") and payload.get("iss") != asynx_config.get("issuer"):
            return "iss_mismatch"
        if asynx_config.get("audience") and payload.get("aud") != asynx_config.get("audience"):
            return "aud_mismatch"
        exp = int(payload.get("exp", 0))
    except (ValueError, TypeError, KeyError):
        return "malformed"
    current = int(time.time() if now is None else now)
    return None if (exp - current) > leeway_seconds else "expired"


def is_token_usable(token: str | None, asynx_config: dict[str, Any] | None, **kwargs: Any) -> bool:
    return jwt_problem(token, asynx_config, **kwargs) is None


def _http_json(method: str, url: str, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
    headers = {"User-Agent": f"asx-skill/{VERSION}", "Accept": "application/json"}
    req = Request(url, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(MAX_API_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        if status == 404:
            raise AsxError(
                "该站点未开放扩展 Skill 凭据（仅 dev 可用）。",
                code="skill_jwt_unavailable", exit_code=2, http_status=404,
            ) from exc
        raise AsxError(
            f"Plaiers 服务 HTTP {status}", code=f"sagein_http_{status}", exit_code=2, http_status=status
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AsxError(f"无法连接 Plaiers 服务 ({url})：{exc}", code="network_error", exit_code=2) from exc
    if len(raw) > MAX_API_RESPONSE_BYTES:
        raise AsxError("Plaiers 响应体超出限制", code="response_too_large", exit_code=2)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AsxError("Plaiers 响应不是合法的 JSON", code="invalid_sagein_response", exit_code=2) from exc
    if not isinstance(parsed, dict):
        raise AsxError("Plaiers 响应格式不正确", code="invalid_sagein_response", exit_code=2)
    code = parsed.get("code")
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        msg = parsed.get("msg") if isinstance(parsed.get("msg"), str) else None
        raise AsxError(msg or f"Plaiers 错误 ({code})", code=f"sagein_error_{code}", exit_code=2)
    data = parsed.get("data", parsed)
    if not isinstance(data, dict):
        raise AsxError("Plaiers 响应 data 字段格式不正确", code="invalid_sagein_response", exit_code=2)
    return data


def fetch_asynx_config(origin: str) -> dict[str, Any]:
    """GET {origin}/api/agent/asynx-config (public)."""
    return _http_json("GET", f"{origin}/api/agent/asynx-config")


class AuthSession:
    """Thread-safe holder for the short-lived JWT and the Asynx gateway config of one process."""

    def __init__(
        self,
        *,
        origin: str | None,
        token: str = "",
        asynx: dict[str, Any] | None = None,
        jwt_command: list[str] | None = None,
        headless_env_token: bool = False,
    ) -> None:
        self.origin = origin
        self.token = token
        self.asynx = dict(asynx or {})
        self.jwt_command = list(jwt_command or [])
        self.headless_env_token = headless_env_token
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        from .config import validate_base_url

        env_base = os.environ.get("ASYNX_BASE_URL")
        if env_base:
            return validate_base_url(env_base)
        configured = self.asynx.get("baseUrl")
        if isinstance(configured, str) and configured.strip():
            return validate_base_url(configured)
        return DEFAULT_BASE_URL

    @property
    def token_header(self) -> str:
        header = self.asynx.get("tokenHeader")
        if isinstance(header, str) and header.strip():
            return header.strip()
        return DEFAULT_TOKEN_HEADER

    def ensure_valid_token(self) -> str:
        if self.headless_env_token:
            return self.token
        with self._lock:
            if is_token_usable(self.token, self.asynx):
                return self.token
            return self._obtain_locked()

    def refresh_session(self) -> str:
        """Called once after the gateway rejected the JWT with 401: ask the bridge again, no other source."""
        if self.headless_env_token:
            raise AsxError(
                "ASYNX_PARTNER_TOKEN 认证失败或已过期，请更新环境变量。",
                code="need_login", exit_code=2, http_status=401,
            )
        with self._lock:
            rejected = self.token
            token = self._obtain_locked()
            if token == rejected:
                raise AsxError(
                    "Asset Gateway 拒绝了当前 JWT，凭据桥接没有给出新的 JWT。请让用户重新授权后再试。",
                    code="need_login",
                    exit_code=2,
                    http_status=401,
                    details={"reason_code": "gateway_rejected_jwt"},
                )
            return token

    def _obtain_locked(self) -> str:
        if not self.asynx.get("baseUrl"):
            self.asynx = fetch_asynx_config(self.origin or "")
        token = run_jwt_bridge(self.jwt_command)
        problem = jwt_problem(token, self.asynx)
        if problem is not None:
            raise AsxError(
                f"凭据桥接返回的 JWT 不可用（{problem}），没有使用其他凭据。",
                code="jwt_invalid",
                exit_code=2,
                details={"reason_code": problem, "origin": self.origin},
            )
        self.token = token
        return token


def load_auth_session() -> AuthSession:
    env_token = os.environ.get("ASYNX_PARTNER_TOKEN")
    if isinstance(env_token, str) and env_token.strip():
        from .config import validate_base_url

        env_base_url = os.environ.get("ASYNX_BASE_URL")
        asynx_cfg = {
            "baseUrl": validate_base_url(env_base_url) if env_base_url else DEFAULT_BASE_URL,
            "tokenHeader": DEFAULT_TOKEN_HEADER,
        }
        return AuthSession(origin=None, token=env_token.strip(), asynx=asynx_cfg, headless_env_token=True)

    context = load_credential_context()
    session = AuthSession(origin=context["origin"], jwt_command=context["jwt_command"])
    session.ensure_valid_token()
    return session
