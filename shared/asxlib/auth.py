"""Short-lived JWT acquisition via the SageIn Agent PAT (see docs/agent-jwt-contract.md).

The long-lived ``sagein_pat_...`` credential stays in the local credential file
written by the sagein-community skill. It is only sent to ``POST /api/agent/jwt``
to exchange for a short-lived JWT; the JWT is the only secret sent to Asynx.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .constants import (
    DEFAULT_BASE_URL,
    DEFAULT_ORIGIN,
    DEFAULT_TOKEN_HEADER,
    MAX_API_RESPONSE_BYTES,
    PROACTIVE_REFRESH_LEEWAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    VERSION,
)
from .errors import AsxError

_PAT_PREFIX = "sagein_pat_"


def validate_origin(value: str | None) -> str:
    candidate = (value or DEFAULT_ORIGIN).strip().rstrip("/")
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
            "SageIn origin 格式不正确，应为 HTTPS 站点根地址（本地测试可用 http://localhost）",
            code="invalid_origin",
            details={"origin": value},
        )
    return candidate


def resolve_origin(config: dict[str, Any] | None = None) -> str:
    env = os.environ.get("SAGEIN_COMMUNITY_ORIGIN")
    if env and env.strip():
        return validate_origin(env)
    configured = (config or {}).get("origin")
    return validate_origin(configured if isinstance(configured, str) else None)


def _community_config_dir() -> Path:
    override = os.environ.get("SAGEIN_COMMUNITY_CONFIG_DIR")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".config" / "sagein-community"


def _read_binding_credential(binding_path: Path, origin: str, agent_id: str | None = None) -> Path | None:
    try:
        data = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("origin") != origin:
        return None
    if agent_id is not None and data.get("agent_id") != agent_id:
        return None
    cred_file = data.get("credential_file")
    if not isinstance(cred_file, str) or not cred_file.strip():
        return None
    candidate = Path(cred_file).expanduser()
    return candidate if candidate.is_file() else None


def _discover_binding_credentials(config_dir: Path, origin: str) -> list[Path]:
    bindings_dir = config_dir / "bindings"
    if not bindings_dir.is_dir():
        return []
    seen: set[Path] = set()
    matches: list[Path] = []
    for binding_path in sorted(bindings_dir.glob("*.json")):
        candidate = _read_binding_credential(binding_path, origin)
        if candidate is not None and candidate not in seen:
            seen.add(candidate)
            matches.append(candidate)
    return matches


def credential_file_path(origin: str) -> Path:
    override = os.environ.get("SAGEIN_COMMUNITY_CREDENTIAL_FILE")
    if override and override.strip():
        return Path(override).expanduser()

    config_dir = _community_config_dir()
    agent_id = os.environ.get("SAGEIN_COMMUNITY_AGENT_ID")
    if agent_id and agent_id.strip():
        aid = agent_id.strip()
        binding_hash = hashlib.sha256(
            json.dumps([origin, aid], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        matched = _read_binding_credential(config_dir / "bindings" / f"{binding_hash}.json", origin, agent_id=aid)
        if matched is not None:
            return matched

    if not (agent_id and agent_id.strip()):
        candidates = _discover_binding_credentials(config_dir, origin)
        if len(candidates) == 1:
            return candidates[0]

    digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]
    return config_dir / "credentials" / f"{digest}.json"


def load_pat(origin: str) -> str:
    path = credential_file_path(origin)
    if (
        not path.is_file()
        and not (os.environ.get("SAGEIN_COMMUNITY_CREDENTIAL_FILE") or "").strip()
        and not (os.environ.get("SAGEIN_COMMUNITY_AGENT_ID") or "").strip()
    ):
        candidates = _discover_binding_credentials(_community_config_dir(), origin)
        if len(candidates) > 1:
            raise AsxError(
                "检测到同一站点下存在多个 SageIn Agent 授权凭据，请设置 SAGEIN_COMMUNITY_AGENT_ID 或 SAGEIN_COMMUNITY_CREDENTIAL_FILE 指定要使用的 Agent。",
                code="need_login",
                exit_code=2,
                details={
                    "reason_code": "ambiguous_credentials",
                    "origin": origin,
                    "candidate_count": len(candidates),
                },
            )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pat = data.get("access_token") if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        pat = None
    if not isinstance(pat, str) or not pat.startswith(_PAT_PREFIX):
        raise AsxError(
            "未找到 SageIn Agent 授权凭据，请先通过 sagein-community 完成授权，并勾选 skill_jwt 权限。",
            code="need_login",
            exit_code=2,
            details={"reason_code": "missing_credentials", "origin": origin, "credential_file": str(path)},
        )
    return pat


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


def is_token_usable(
    token: str | None,
    asynx_config: dict[str, Any] | None,
    *,
    now: float | None = None,
    leeway_seconds: int = PROACTIVE_REFRESH_LEEWAY_SECONDS,
) -> bool:
    if not isinstance(token, str) or not token.strip():
        return False
    if not isinstance(asynx_config, dict) or not asynx_config.get("baseUrl"):
        return False
    try:
        header, payload = decode_jwt(token.strip())
        if asynx_config.get("kid") and header.get("kid") != asynx_config.get("kid"):
            return False
        if asynx_config.get("issuer") and payload.get("iss") != asynx_config.get("issuer"):
            return False
        if asynx_config.get("audience") and payload.get("aud") != asynx_config.get("audience"):
            return False
        exp = int(payload.get("exp", 0))
        current = int(time.time() if now is None else now)
        return (exp - current) > leeway_seconds
    except (ValueError, TypeError, KeyError):
        return False


def _http_json(
    method: str,
    url: str,
    *,
    pat: str | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    headers = {"User-Agent": f"asx-skill/{VERSION}", "Accept": "application/json"}
    body: bytes | None = None
    if method == "POST":
        headers["Content-Type"] = "application/json"
        body = b"{}"
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(MAX_API_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        if status == 401:
            raise AsxError(
                "SageIn Agent 授权已过期或被撤销，请重新授权。",
                code="need_login", exit_code=2, http_status=401,
                details={"reason_code": "pat_invalid"},
            ) from exc
        if status == 403:
            raise AsxError(
                "当前 SageIn 授权缺少 skill_jwt 权限，请重新授权并勾选该权限。",
                code="scope_missing", exit_code=2, http_status=403,
                details={"required_scope": "skill_jwt"},
            ) from exc
        raise AsxError(
            f"SageIn 服务 HTTP {status}", code=f"sagein_http_{status}", exit_code=2, http_status=status
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AsxError(f"无法连接 SageIn 服务 ({url})：{exc}", code="network_error", exit_code=2) from exc
    if len(raw) > MAX_API_RESPONSE_BYTES:
        raise AsxError("SageIn 响应体超出限制", code="response_too_large", exit_code=2)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AsxError("SageIn 响应不是合法的 JSON", code="invalid_sagein_response", exit_code=2) from exc
    if not isinstance(parsed, dict):
        raise AsxError("SageIn 响应格式不正确", code="invalid_sagein_response", exit_code=2)
    code = parsed.get("code")
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        msg = parsed.get("msg") if isinstance(parsed.get("msg"), str) else None
        raise AsxError(msg or f"SageIn 错误 ({code})", code=f"sagein_error_{code}", exit_code=2)
    data = parsed.get("data", parsed)
    if not isinstance(data, dict):
        raise AsxError("SageIn 响应 data 字段格式不正确", code="invalid_sagein_response", exit_code=2)
    return data


def fetch_asynx_config(origin: str) -> dict[str, Any]:
    """GET {origin}/api/agent/asynx-config (public)."""
    return _http_json("GET", f"{origin}/api/agent/asynx-config")


def fetch_jwt(origin: str, pat: str) -> str:
    """POST {origin}/api/agent/jwt with the PAT to obtain a short-lived JWT."""
    data = _http_json("POST", f"{origin}/api/agent/jwt", pat=pat)
    for key in ("token", "jwt", "access_token"):
        value = data.get(key)
        if isinstance(value, str) and value.count(".") == 2:
            return value
    raise AsxError("SageIn JWT 响应缺少 token 字段", code="invalid_sagein_response", exit_code=2)


class AuthSession:
    """Thread-safe holder for the short-lived JWT and the Asynx gateway config."""

    def __init__(
        self,
        *,
        origin: str,
        token: str,
        asynx: dict[str, Any] | None = None,
        headless_env_token: bool = False,
    ) -> None:
        self.origin = origin
        self.token = token
        self.asynx = dict(asynx or {})
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
            return self._refresh_locked()

    def refresh_session(self) -> str:
        if self.headless_env_token:
            raise AsxError(
                "ASYNX_PARTNER_TOKEN 认证失败或已过期，请更新环境变量。",
                code="need_login", exit_code=2, http_status=401,
            )
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> str:
        pat = load_pat(self.origin)
        try:
            self.asynx = fetch_asynx_config(self.origin)
        except AsxError as exc:
            if exc.code != "network_error" or not self.asynx.get("baseUrl"):
                raise
        self.token = fetch_jwt(self.origin, pat)
        self._persist()
        return self.token

    def _persist(self) -> None:
        from .config import _write_config, config_path, read_config

        try:
            current = read_config()
            current.pop("refreshToken", None)
            current.pop("user", None)
            current.pop("login_svr_url", None)
            current["token"] = self.token
            current["asynx"] = self.asynx
            _write_config(current, config_path())
        except AsxError:
            pass  # cache is best-effort


def load_auth_session() -> AuthSession:
    from .config import read_config, validate_base_url

    config = read_config()
    origin = resolve_origin(config)

    env_token = os.environ.get("ASYNX_PARTNER_TOKEN")
    if isinstance(env_token, str) and env_token.strip():
        asynx_cfg: dict[str, Any] = {}
        env_base_url = os.environ.get("ASYNX_BASE_URL")
        asynx_cfg["baseUrl"] = validate_base_url(env_base_url) if env_base_url else DEFAULT_BASE_URL
        asynx_cfg["tokenHeader"] = DEFAULT_TOKEN_HEADER
        return AuthSession(origin=origin, token=env_token.strip(), asynx=asynx_cfg, headless_env_token=True)

    cached_token = config.get("token") if isinstance(config.get("token"), str) else ""
    cached_asynx = dict(config.get("asynx")) if isinstance(config.get("asynx"), dict) else {}
    session = AuthSession(origin=origin, token=cached_token, asynx=cached_asynx)
    session.ensure_valid_token()  # refreshes via PAT when missing / near expiry
    return session
