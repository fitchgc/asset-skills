from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

from .constants import (
    DEFAULT_BASE_URL,
    DEFAULT_LOGIN_SVR_URL,
    DEFAULT_TOKEN_HEADER,
    MAX_API_RESPONSE_BYTES,
    MAX_HTTP_ATTEMPTS,
    PROACTIVE_REFRESH_LEEWAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    TRANSIENT_HTTP_STATUSES,
    VERSION,
)
from .errors import AsxError

_LOGIN_SVR_ERROR_MAP: dict[int, tuple[str, str]] = {
    10: ("need_login", "未登录或缺少登录凭证"),
    11: ("need_login", "账户不存在，请重新登录"),
    12: ("account_locked", "账号已被锁定，请联系管理员"),
    15: ("account_deleted", "账号已注销"),
    401: ("auth_expired", "登录凭证已过期或失效"),
    1001: ("invalid_credentials_format", "用户名或密码格式不正确"),
    1002: ("username_exists", "用户名已被注册"),
    1003: ("invalid_login", "用户名或密码错误"),
    1004: ("invalid_password_change", "原密码错误或新密码与原密码相同"),
}

UNREFRESHABLE_LOGIN_SVR_CODES = frozenset({10, 11, 12, 15, 401})

LoginUIHandler = Callable[[str], dict[str, Any] | None]
_login_ui_handler: LoginUIHandler | None = None
_login_ui_cli_disabled: bool = False


def register_login_ui_handler(handler: LoginUIHandler | None) -> None:
    """Register the interactive Login Workbench launcher hook."""
    global _login_ui_handler
    _login_ui_handler = handler


def set_login_ui_disabled(disabled: bool) -> None:
    global _login_ui_cli_disabled
    _login_ui_cli_disabled = bool(disabled)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_login_ui_disabled() -> bool:
    if _login_ui_cli_disabled or _truthy_env("ASYNX_NO_LOGIN_UI") or _truthy_env("CI"):
        return True
    if (
        os.name != "nt"
        and not sys.platform.startswith("darwin")
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
        and not _truthy_env("ASYNX_NO_OPEN_BROWSER")
    ):
        return True
    return False


def is_login_ui_allowed(explicit: bool | None = None) -> bool:
    if explicit is False:
        return False
    if is_login_ui_disabled():
        return False
    return _login_ui_handler is not None


def validate_login_svr_url(value: str | None) -> str:
    candidate = (value or DEFAULT_LOGIN_SVR_URL).strip().rstrip("/")
    parts = urlsplit(candidate)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.query
        or parts.fragment
    ):
        raise AsxError(
            "Login Server URL 格式不正确，应形如 http://localhost:3006",
            code="invalid_login_svr_url",
            details={"login_svr_url": value},
        )
    return candidate


def resolve_login_svr_url(config: dict[str, Any] | None = None) -> str:
    env_url = os.environ.get("ASYNX_LOGIN_SVR_URL")
    if env_url:
        return validate_login_svr_url(env_url)
    stored = config.get("login_svr_url") if isinstance(config, dict) else None
    if isinstance(stored, str) and stored.strip():
        return validate_login_svr_url(stored)
    return DEFAULT_LOGIN_SVR_URL


def decode_jwt_segment(segment: str) -> dict[str, Any]:
    padding = "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment + padding)
    decoded = json.loads(raw.decode("utf-8"))
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
    except Exception:
        return False


def _map_login_svr_error(
    code_val: int,
    msg: str | None,
    *,
    http_status: int = 200,
) -> AsxError:
    mapped_code, default_msg = _LOGIN_SVR_ERROR_MAP.get(
        code_val, (f"login_svr_error_{code_val}", msg or f"Identity Service 错误 ({code_val})")
    )
    message = f"{default_msg}：{msg}" if msg and msg != default_msg else (msg or default_msg)
    return AsxError(
        message,
        code=mapped_code,
        exit_code=2,
        http_status=http_status,
        details={
            "login_svr_code": code_val,
            "login_svr_msg": msg,
        },
    )


class LoginSvrClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = validate_login_svr_url(base_url)

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        url = self._url(path)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {
            "User-Agent": f"asx-skill/{VERSION}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"

        for attempt in range(MAX_HTTP_ATTEMPTS):
            req = Request(url, data=payload, headers=headers, method=method)
            try:
                with urlopen(req, timeout=timeout) as response:
                    status = getattr(response, "status", 200)
                    raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_API_RESPONSE_BYTES:
                        raise AsxError(
                            "Identity Service 响应体超出限制",
                            code="response_too_large",
                            http_status=status,
                        )
                    return self._parse_envelope(raw, status=status)
            except HTTPError as exc:
                raw = exc.read(MAX_API_RESPONSE_BYTES)
                if exc.code in TRANSIENT_HTTP_STATUSES and attempt < MAX_HTTP_ATTEMPTS - 1:
                    time.sleep(min(0.5 * (2**attempt), 5.0))
                    continue
                # If login_svr returned a structured JSON error even on non-200, map it
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                    if isinstance(parsed, dict) and isinstance(parsed.get("code"), int) and not isinstance(parsed.get("code"), bool) and parsed["code"] != 0:
                        raise _map_login_svr_error(
                            parsed["code"],
                            parsed.get("msg") if isinstance(parsed.get("msg"), str) else None,
                            http_status=exc.code,
                        )
                except AsxError:
                    raise
                except Exception:
                    pass
                raise AsxError(
                    f"Identity Service HTTP {exc.code}",
                    code=f"login_svr_http_{exc.code}",
                    exit_code=2,
                    http_status=exc.code,
                ) from exc
            except (URLError, TimeoutError) as exc:
                if attempt < MAX_HTTP_ATTEMPTS - 1:
                    time.sleep(min(0.5 * (2**attempt), 5.0))
                    continue
                raise AsxError(
                    f"无法连接 Identity Service ({self.base_url})：{exc}",
                    code="network_error",
                    exit_code=2,
                ) from exc
        raise AsxError("Identity Service 请求失败", code="network_error", exit_code=2)

    @staticmethod
    def _parse_envelope(raw: bytes, *, status: int) -> dict[str, Any]:
        if status != 200:
            raise AsxError(
                f"Identity Service 返回异常 HTTP 状态 {status}",
                code=f"login_svr_http_{status}",
                exit_code=2,
                http_status=status,
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AsxError(
                "Identity Service 响应不是合法的 JSON",
                code="invalid_login_svr_response",
                exit_code=2,
            ) from exc
        if not isinstance(parsed, dict):
            raise AsxError(
                "Identity Service 响应格式不正确，应为 JSON 对象",
                code="invalid_login_svr_response",
                exit_code=2,
            )
        code = parsed.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise AsxError(
                "Identity Service 响应缺少数字 code 字段",
                code="invalid_login_svr_response",
                exit_code=2,
            )
        if code != 0:
            msg = parsed.get("msg") if isinstance(parsed.get("msg"), str) else None
            raise _map_login_svr_error(code, msg, http_status=status)
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise AsxError(
                "Identity Service 响应 data 字段格式不正确",
                code="invalid_login_svr_response",
                exit_code=2,
            )
        return data

    def asynx_config(self) -> dict[str, Any]:
        return self.request("GET", "/api/user/asynx-config")

    def check_username(self, username: str) -> dict[str, Any]:
        return self.request("GET", f"/api/user/check-username?username={quote(username, safe='')}")

    def register(self, username: str, password: str, nickname: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"username": username, "password": password}
        if nickname is not None:
            body["nickname"] = nickname
        return self.request("POST", "/api/user/register", body=body)

    def login(self, username: str, password: str) -> dict[str, Any]:
        return self.request("POST", "/api/user/login", body={"username": username, "password": password})

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self.request("POST", "/api/user/refresh", body={"refreshToken": refresh_token})

    def profile(self, token: str) -> dict[str, Any]:
        return self.request("GET", "/api/user/profile", token=token)

    def change_password(self, token: str, old_password: str, new_password: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/user/change-password",
            body={"oldPassword": old_password, "newPassword": new_password},
            token=token,
        )


def save_auth_session(session_data: dict[str, Any], *, login_svr_url: str | None = None) -> dict[str, Any]:
    from .config import _write_config, config_path, read_config

    current = read_config()
    resolved_login_url = validate_login_svr_url(
        login_svr_url or resolve_login_svr_url(current)
    )
    current.pop("api_key", None)
    current["login_svr_url"] = resolved_login_url
    if isinstance(session_data.get("token"), str):
        current["token"] = session_data["token"]
    if isinstance(session_data.get("refreshToken"), str):
        current["refreshToken"] = session_data["refreshToken"]
    if isinstance(session_data.get("asynx"), dict):
        current["asynx"] = session_data["asynx"]
    if isinstance(session_data.get("user"), dict):
        current["user"] = session_data["user"]
    _write_config(current, config_path())
    return current


def clear_auth_session(*, keep_login_svr_url: bool = True) -> None:
    from .config import _write_config, config_path, read_config

    current = read_config()
    if not current:
        return
    for key in ("token", "refreshToken", "user", "api_key"):
        current.pop(key, None)
    if not keep_login_svr_url:
        current.pop("login_svr_url", None)
    _write_config(current, config_path())


class AuthSession:
    """Thread-safe holder for Identity Service session credentials and refresh state."""

    def __init__(
        self,
        *,
        login_svr_url: str,
        token: str,
        refresh_token: str | None = None,
        asynx: dict[str, Any] | None = None,
        user: dict[str, Any] | None = None,
        headless_env_token: bool = False,
        allow_login_ui: bool = True,
    ) -> None:
        self.login_svr_url = validate_login_svr_url(login_svr_url)
        self.token = token
        self.refresh_token = refresh_token
        self.asynx = dict(asynx or {})
        self.user = dict(user or {})
        self.headless_env_token = headless_env_token
        self.allow_login_ui = allow_login_ui
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
        if self.headless_env_token and not self.refresh_token:
            raise AsxError(
                "ASYNX_PARTNER_TOKEN 认证失败或已过期，请更新环境变量或登录账号。",
                code="need_login",
                exit_code=2,
                http_status=401,
            )
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> str:
        if not self.refresh_token:
            return self._trigger_login_or_raise("missing_refresh_token")

        client = LoginSvrClient(self.login_svr_url)
        try:
            refreshed = client.refresh(self.refresh_token)
        except AsxError as exc:
            login_svr_code = exc.details.get("login_svr_code") if isinstance(exc.details, dict) else None
            if login_svr_code in UNREFRESHABLE_LOGIN_SVR_CODES:
                clear_auth_session()
                self.token = ""
                self.refresh_token = None
                return self._trigger_login_or_raise(exc.code, login_svr_code=login_svr_code, message=exc.message)
            raise

        self._apply_session_payload(refreshed)
        save_auth_session(refreshed, login_svr_url=self.login_svr_url)
        return self.token

    def _apply_session_payload(self, data: dict[str, Any]) -> None:
        self.token = str(data["token"])
        self.refresh_token = str(data["refreshToken"]) if data.get("refreshToken") else self.refresh_token
        if isinstance(data.get("asynx"), dict):
            self.asynx = dict(data["asynx"])
        if isinstance(data.get("user"), dict):
            self.user = dict(data["user"])

    def _trigger_login_or_raise(
        self,
        reason_code: str,
        *,
        login_svr_code: int | None = None,
        message: str | None = None,
    ) -> str:
        if is_login_ui_allowed(self.allow_login_ui) and _login_ui_handler is not None:
            new_data = _login_ui_handler(self.login_svr_url)
            if isinstance(new_data, dict) and isinstance(new_data.get("token"), str):
                if isinstance(new_data.get("login_svr_url"), str) and new_data["login_svr_url"].strip():
                    self.login_svr_url = validate_login_svr_url(new_data["login_svr_url"])
                self._apply_session_payload(new_data)
                return self.token
        raise AsxError(
            message or "登录会话已过期或未登录，请重新登录。",
            code="need_login",
            exit_code=2,
            http_status=401,
            details={
                "reason_code": reason_code,
                **({"login_svr_code": login_svr_code} if login_svr_code is not None else {}),
                "login_svr_url": self.login_svr_url,
            },
        )


def _sync_asynx_config(login_svr_url: str, cached_asynx: dict[str, Any]) -> dict[str, Any]:
    client = LoginSvrClient(login_svr_url)
    try:
        latest = client.asynx_config()
        return latest
    except AsxError as exc:
        # If asynx-config is unreachable and we already have a cached asynx config with baseUrl,
        # let cached config stand unless token itself needs refresh.
        if exc.code == "network_error" and isinstance(cached_asynx, dict) and cached_asynx.get("baseUrl"):
            return cached_asynx
        raise


def load_auth_session(*, allow_login_ui: bool = True, sync_config: bool = True) -> AuthSession:
    from .config import _write_config, config_path, read_config, validate_base_url

    config = read_config()
    login_svr_url = resolve_login_svr_url(config)

    # Headless direct token override
    env_token = os.environ.get("ASYNX_PARTNER_TOKEN")
    if isinstance(env_token, str) and env_token.strip():
        asynx_cfg = dict(config.get("asynx")) if isinstance(config.get("asynx"), dict) else {}
        env_base_url = os.environ.get("ASYNX_BASE_URL")
        if env_base_url:
            asynx_cfg["baseUrl"] = validate_base_url(env_base_url)
        elif not asynx_cfg.get("baseUrl"):
            stored_base = config.get("base_url")
            asynx_cfg["baseUrl"] = (
                validate_base_url(stored_base)
                if isinstance(stored_base, str) and stored_base.strip()
                else DEFAULT_BASE_URL
            )
        asynx_cfg.setdefault("tokenHeader", DEFAULT_TOKEN_HEADER)
        return AuthSession(
            login_svr_url=login_svr_url,
            token=env_token.strip(),
            refresh_token=config.get("refreshToken") if isinstance(config.get("refreshToken"), str) else None,
            asynx=asynx_cfg,
            user=config.get("user") if isinstance(config.get("user"), dict) else None,
            headless_env_token=True,
            allow_login_ui=allow_login_ui,
        )

    token = config.get("token") if isinstance(config.get("token"), str) else ""
    refresh_token = config.get("refreshToken") if isinstance(config.get("refreshToken"), str) else None
    cached_asynx = dict(config.get("asynx")) if isinstance(config.get("asynx"), dict) else {}
    user = dict(config.get("user")) if isinstance(config.get("user"), dict) else {}

    if not token and not refresh_token:
        if is_login_ui_allowed(allow_login_ui) and _login_ui_handler is not None:
            new_data = _login_ui_handler(login_svr_url)
            if isinstance(new_data, dict) and isinstance(new_data.get("token"), str):
                updated_cfg = read_config()
                resolved_url = (
                    validate_login_svr_url(new_data["login_svr_url"])
                    if isinstance(new_data.get("login_svr_url"), str) and new_data["login_svr_url"].strip()
                    else resolve_login_svr_url(updated_cfg)
                )
                return AuthSession(
                    login_svr_url=resolved_url,
                    token=new_data["token"],
                    refresh_token=new_data.get("refreshToken"),
                    asynx=new_data.get("asynx") if isinstance(new_data.get("asynx"), dict) else {},
                    user=new_data.get("user") if isinstance(new_data.get("user"), dict) else {},
                    allow_login_ui=allow_login_ui,
                )
        raise AsxError(
            "未检测到 Asynx 登录会话，请先登录。",
            code="need_login",
            exit_code=2,
            details={"reason_code": "missing_credentials", "login_svr_url": login_svr_url},
        )

    asynx_cfg = cached_asynx
    if sync_config or not asynx_cfg.get("baseUrl"):
        latest_asynx = _sync_asynx_config(login_svr_url, cached_asynx)
        if latest_asynx != cached_asynx:
            asynx_cfg = latest_asynx
            if config_path().exists():
                updated = dict(config)
                updated["asynx"] = asynx_cfg
                _write_config(updated, config_path())

    env_base_url = os.environ.get("ASYNX_BASE_URL")
    if env_base_url:
        asynx_cfg["baseUrl"] = validate_base_url(env_base_url)

    session = AuthSession(
        login_svr_url=login_svr_url,
        token=token,
        refresh_token=refresh_token,
        asynx=asynx_cfg,
        user=user,
        allow_login_ui=allow_login_ui,
    )
    session.ensure_valid_token()
    return session


def login_and_provision(
    username: str,
    password: str,
    *,
    login_svr_url: str | None = None,
) -> tuple[AuthSession, dict[str, Any]]:
    from .client import AsynxClient

    resolved_url = validate_login_svr_url(login_svr_url or resolve_login_svr_url())
    login_client = LoginSvrClient(resolved_url)
    data = login_client.login(username, password)
    save_auth_session(data, login_svr_url=resolved_url)
    session = AuthSession(
        login_svr_url=resolved_url,
        token=data["token"],
        refresh_token=data.get("refreshToken"),
        asynx=data.get("asynx") if isinstance(data.get("asynx"), dict) else {},
        user=data.get("user") if isinstance(data.get("user"), dict) else {},
    )
    gateway_client = AsynxClient(session.base_url, session=session)
    identity, _req_id = gateway_client.partner_identity()
    return session, identity


def register_and_provision(
    username: str,
    password: str,
    nickname: str | None = None,
    *,
    login_svr_url: str | None = None,
) -> tuple[AuthSession, dict[str, Any]]:
    from .client import AsynxClient

    resolved_url = validate_login_svr_url(login_svr_url or resolve_login_svr_url())
    login_client = LoginSvrClient(resolved_url)
    data = login_client.register(username, password, nickname=nickname)
    save_auth_session(data, login_svr_url=resolved_url)
    session = AuthSession(
        login_svr_url=resolved_url,
        token=data["token"],
        refresh_token=data.get("refreshToken"),
        asynx=data.get("asynx") if isinstance(data.get("asynx"), dict) else {},
        user=data.get("user") if isinstance(data.get("user"), dict) else {},
    )
    gateway_client = AsynxClient(session.base_url, session=session)
    identity, _req_id = gateway_client.partner_identity()
    return session, identity
