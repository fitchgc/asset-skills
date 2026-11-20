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
    DEFAULT_TOKEN_HEADER,
    MAX_API_RESPONSE_BYTES,
    PROACTIVE_REFRESH_LEEWAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    VERSION,
)
from .errors import AsxError
from .output import log

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


def jwt_subject(token: str) -> str | None:
    """Account the JWT was issued to (``sub``). The bridge already checked it against the binding."""
    try:
        subject = decode_jwt(token)[1].get("sub")
    except (ValueError, TypeError):
        return None
    return subject if isinstance(subject, str) and subject else None


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
        identity: dict[str, str] | None = None,
    ) -> None:
        self.origin = origin
        self.token = token
        self.asynx = dict(asynx or {})
        self.jwt_command = list(jwt_command or [])
        # Site + Agent + account this session acts as; fixed for the life of the session.
        self.identity = dict(identity) if identity else None
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        from .config import validate_base_url

        # Only the site's asynx-config decides the gateway: no environment override, no default gateway.
        configured = self.asynx.get("baseUrl")
        if isinstance(configured, str) and configured.strip():
            return validate_base_url(configured)
        raise AsxError(
            "站点的 asynx-config 没有给出资产网关地址，未使用任何默认网关。",
            code="asynx_config_invalid",
            exit_code=2,
            details={"origin": self.origin},
        )

    @property
    def token_header(self) -> str:
        header = self.asynx.get("tokenHeader")
        if isinstance(header, str) and header.strip():
            return header.strip()
        return DEFAULT_TOKEN_HEADER

    def ensure_valid_token(self) -> str:
        with self._lock:
            if is_token_usable(self.token, self.asynx):
                return self.token
            return self._obtain_locked()

    def refresh_session(self) -> str:
        """Called once after the gateway rejected the JWT with 401: ask the bridge again, no other source."""
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
            self.base_url  # asynx_config_invalid when the site gives no gateway; nothing else is tried
        token = run_jwt_bridge(self.jwt_command)
        problem = jwt_problem(token, self.asynx)
        if problem is not None:
            raise AsxError(
                f"凭据桥接返回的 JWT 不可用（{problem}），没有使用其他凭据。",
                code="jwt_invalid",
                exit_code=2,
                details={"reason_code": problem, "origin": self.origin},
            )
        if self.identity is not None and jwt_subject(token) != self.identity["account_id"]:
            raise AsxError(
                "凭据桥接返回了另一个账号的 JWT；本次会话绑定的账号不变，没有使用这个 JWT。",
                code="jwt_invalid",
                exit_code=2,
                details={"reason_code": "account_changed", "origin": self.origin},
            )
        self.token = token
        return token


# Direct credential injection removed by PR #10 Q6: interactive and headless (CI) runs both obtain the JWT
# only through the recorded get-skill-jwt.mjs argv. Setting these is an error, never silently ignored.
LEGACY_CREDENTIAL_ENV = ("ASYNX_PARTNER_TOKEN", "ASYNX_BASE_URL")


def legacy_credential_env() -> list[str]:
    return [name for name in LEGACY_CREDENTIAL_ENV if (os.environ.get(name) or "").strip()]


def reject_legacy_credential_env() -> None:
    found = legacy_credential_env()
    if found:
        raise AsxError(
            f"{' / '.join(found)} 已不再支持：本 Skill 不接受直接注入的 JWT 或网关地址，也不会回退到它们。"
            "请取消这些环境变量，改用 Plaiers 社区凭据桥接 get-skill-jwt.mjs；"
            "无界面 CI 需在 .plaiers-context.json（或 PLAIERS_SKILL_CONTEXT 指向的文件）中显式绑定 "
            "--origin、--agent-id、--account-id 和 --credential-file（由 CI secret 或受限文件提供）。",
            code="legacy_credentials_removed",
            exit_code=2,
            details={"variables": found, "migration": "docs/agent-jwt-contract.md#无界面-ci"},
        )


# Ledger identity per credential context (path + content), resolved at most once per process.
_LEDGER_IDENTITIES: dict[str, dict[str, str] | None] = {}


def _context_key() -> str | None:
    path = context_path()
    try:
        return f"{path}\0{path.read_text(encoding='utf-8')}"
    except OSError:
        return None


def ledger_identity() -> dict[str, str] | None:
    """Site + Agent + account that owns this installation's task ledger, or None without a usable context.

    The account is the one pinned by ``--account-id``; otherwise it is the ``sub`` of the JWT the bridge
    issues for this context (the bridge checks it against the binding). Nothing else is consulted: no
    scan of other bindings, no remembered account from an earlier run.
    """
    key = _context_key()
    if key is None:
        return None
    if key not in _LEDGER_IDENTITIES:
        identity: dict[str, str] | None = None
        try:
            context = load_credential_context()
            account = context["account_id"] or jwt_subject(run_jwt_bridge(context["jwt_command"]))
            if account:
                identity = {"origin": context["origin"], "agent_id": context["agent_id"], "account_id": account}
        except AsxError as exc:
            log(f"无法确认当前账号（{exc.code}），本次只使用未绑定身份的本机账本，不读取任何账号的任务记录。")
        _LEDGER_IDENTITIES[key] = identity
    return _LEDGER_IDENTITIES[key]


def load_auth_session() -> AuthSession:
    reject_legacy_credential_env()
    context = load_credential_context()
    key = _context_key()
    session = AuthSession(origin=context["origin"], jwt_command=context["jwt_command"])
    account = jwt_subject(session.ensure_valid_token())
    if not account or (context["account_id"] and context["account_id"] != account):
        raise AsxError(
            "凭据桥接返回的 JWT 与凭据上下文固定的账号不一致，没有使用其他凭据。",
            code="jwt_invalid",
            exit_code=2,
            details={"reason_code": "account_mismatch", "origin": context["origin"]},
        )
    session.identity = {"origin": context["origin"], "agent_id": context["agent_id"], "account_id": account}
    if key is not None:
        _LEDGER_IDENTITIES[key] = dict(session.identity)
    return session
