from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_test_jwt(
    *,
    sub: str = "user_1",
    kid: str = "k1",
    issuer: str = "login_svr",
    audience: str = "asynx",
    exp: int | None = None,
    iat: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now if iat is None else iat,
        "exp": (now + 86400) if exp is None else exp,
        "jti": secrets.token_hex(8),
    }
    if extra_claims:
        payload.update(extra_claims)
    h_seg = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_seg = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(f"sig-{secrets.token_hex(8)}".encode("utf-8"))
    return f"{h_seg}.{p_seg}.{sig}"


class FakeLoginHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _bearer_user(self) -> dict[str, Any] | None:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer "):].strip()
        username = self.server.active_tokens.get(token)
        if not username:
            return None
        return self.server.users.get(username)

    def _issue_session(self, user_rec: dict[str, Any], *, exp: int | None = None) -> dict[str, Any]:
        cfg = dict(self.server.asynx_config)
        token = make_test_jwt(
            sub=user_rec["id"],
            kid=cfg["kid"],
            issuer=cfg["issuer"],
            audience=cfg["audience"],
            exp=exp if exp is not None else int(time.time()) + int(cfg.get("tokenExpiresIn", 86400)),
        )
        refresh_token = f"rt_{secrets.token_hex(12)}"
        self.server.active_tokens[token] = user_rec["username"]
        self.server.refresh_tokens[refresh_token] = user_rec["username"]
        user_payload = {
            "id": user_rec["id"],
            "username": user_rec["username"],
            "nickname": user_rec["nickname"],
            "locked": user_rec["locked"],
            "deleted": user_rec["deleted"],
            "lastLogin": user_rec["lastLogin"],
            "createdAt": user_rec["createdAt"],
        }
        return {
            "token": token,
            "refreshToken": refresh_token,
            "asynx": cfg,
            "user": user_payload,
        }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        self.server.requests.append(("GET", path, dict(self.headers)))

        if path == "/api/user/asynx-config":
            self._send_json({"code": 0, "data": dict(self.server.asynx_config)})
            return

        if path == "/api/user/check-username":
            qs = parse_qs(parsed.query)
            username = (qs.get("username") or [""])[0].strip().lower()
            if not _USERNAME_RE.match(username):
                self._send_json({"code": 1001, "msg": "invalid username format"})
                return
            exists = username in self.server.users
            self._send_json({"code": 0, "data": {"exists": exists, "available": not exists}})
            return

        if path == "/api/user/profile":
            auth = self.headers.get("Authorization", "")
            if not auth:
                self._send_json({"code": 10, "msg": "need login"})
                return
            user_rec = self._bearer_user()
            if user_rec is None:
                self._send_json({"code": 401, "msg": "need auth"})
                return
            if user_rec["locked"]:
                self._send_json({"code": 12, "msg": "account locked"})
                return
            if user_rec["deleted"]:
                self._send_json({"code": 15, "msg": "account deleted"})
                return
            self._send_json({
                "code": 0,
                "data": {
                    "id": user_rec["id"],
                    "username": user_rec["username"],
                    "nickname": user_rec["nickname"],
                    "locked": user_rec["locked"],
                    "deleted": user_rec["deleted"],
                },
            })
            return

        self._send_json({"code": 404, "msg": "not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()
        self.server.requests.append(("POST", path, dict(self.headers), body))

        if path == "/api/user/register":
            username = str(body.get("username") or "").strip().lower()
            password = str(body.get("password") or "")
            nickname = str(body.get("nickname") or username)
            if not _USERNAME_RE.match(username) or not (6 <= len(password) <= 64):
                self._send_json({"code": 1001, "msg": "invalid username format or password length"})
                return
            if username in self.server.users:
                self._send_json({"code": 1002, "msg": "username already exists"})
                return
            now_iso = "2026-11-14T01:30:00.000Z"
            user_rec = {
                "id": f"user_{len(self.server.users) + 1}",
                "username": username,
                "password": password,
                "nickname": nickname,
                "locked": False,
                "deleted": False,
                "lastLogin": now_iso,
                "createdAt": now_iso,
            }
            self.server.users[username] = user_rec
            self._send_json({"code": 0, "data": self._issue_session(user_rec)})
            return

        if path == "/api/user/login":
            username = str(body.get("username") or "").strip().lower()
            password = str(body.get("password") or "")
            user_rec = self.server.users.get(username)
            if not user_rec or user_rec["password"] != password:
                self._send_json({"code": 1003, "msg": "invalid username or password"})
                return
            if user_rec["locked"]:
                self._send_json({"code": 12, "msg": "account locked"})
                return
            if user_rec["deleted"]:
                self._send_json({"code": 15, "msg": "account deleted"})
                return
            self._send_json({"code": 0, "data": self._issue_session(user_rec)})
            return

        if path == "/api/user/refresh":
            if self.server.forced_refresh_error is not None:
                code, msg = self.server.forced_refresh_error
                self._send_json({"code": code, "msg": msg})
                return
            rt = str(body.get("refreshToken") or "")
            if not rt:
                self._send_json({"code": 10, "msg": "need login"})
                return
            username = self.server.refresh_tokens.pop(rt, None)
            if not username:
                self._send_json({"code": 401, "msg": "need auth"})
                return
            user_rec = self.server.users.get(username)
            if not user_rec:
                self._send_json({"code": 11, "msg": "need login"})
                return
            if user_rec["locked"]:
                self._send_json({"code": 12, "msg": "account locked"})
                return
            if user_rec["deleted"]:
                self._send_json({"code": 15, "msg": "account deleted"})
                return
            self._send_json({"code": 0, "data": self._issue_session(user_rec)})
            return

        if path == "/api/user/change-password":
            auth = self.headers.get("Authorization", "")
            if not auth:
                self._send_json({"code": 10, "msg": "need login"})
                return
            user_rec = self._bearer_user()
            if user_rec is None:
                self._send_json({"code": 401, "msg": "need auth"})
                return
            old_pw = str(body.get("oldPassword") or "")
            new_pw = str(body.get("newPassword") or "")
            if old_pw != user_rec["password"] or old_pw == new_pw or not (6 <= len(new_pw) <= 64):
                self._send_json({"code": 1004, "msg": "invalid old password or new password invalid"})
                return
            user_rec["password"] = new_pw
            # Invalidate old tokens for this user
            for tok in [t for t, u in self.server.active_tokens.items() if u == user_rec["username"]]:
                self.server.active_tokens.pop(tok, None)
            for rt in [r for r, u in self.server.refresh_tokens.items() if u == user_rec["username"]]:
                self.server.refresh_tokens.pop(rt, None)
            self._send_json({"code": 0, "data": self._issue_session(user_rec)})
            return

        self._send_json({"code": 404, "msg": "not found"}, status=404)


class FakeLoginServer(HTTPServer):
    def __init__(self, gateway_base_url: str = "http://127.0.0.1:9999", host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), FakeLoginHandler)
        self.asynx_config: dict[str, Any] = {
            "baseUrl": gateway_base_url.rstrip("/"),
            "tokenHeader": "X-Asynx-Partner-Token",
            "issuer": "login_svr",
            "audience": "asynx",
            "kid": "k1",
            "tokenExpiresIn": 86400,
        }
        self.users: dict[str, dict[str, Any]] = {}
        self.active_tokens: dict[str, str] = {}
        self.refresh_tokens: dict[str, str] = {}
        self.requests: list[tuple[Any, ...]] = []
        self.forced_refresh_error: tuple[int, str] | None = None

    def seed_user(
        self,
        username: str = "alice_01",
        password: str = "secret_123",
        nickname: str = "Alice",
        *,
        locked: bool = False,
        deleted: bool = False,
    ) -> dict[str, Any]:
        now_iso = "2026-11-14T01:30:00.000Z"
        rec = {
            "id": f"user_{len(self.users) + 1}",
            "username": username,
            "password": password,
            "nickname": nickname,
            "locked": locked,
            "deleted": deleted,
            "lastLogin": now_iso,
            "createdAt": now_iso,
        }
        self.users[username] = rec
        return rec


def run_fake_login_server(gateway_base_url: str = "http://127.0.0.1:9999") -> tuple[FakeLoginServer, str, threading.Thread]:
    server = FakeLoginServer(gateway_base_url=gateway_base_url)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, url, thread
