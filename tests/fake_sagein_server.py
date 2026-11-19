"""Fake SageIn server: GET /api/agent/asynx-config and POST /api/agent/jwt."""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

PAT = "sagein_pat_" + "a" * 64


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_test_jwt(
    *,
    sub: str = "user_1",
    kid: str = "k1",
    issuer: str = "login_svr",
    audience: str = "asynx",
    exp: int | None = None,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    payload = {
        "sub": sub, "iss": issuer, "aud": audience, "iat": now,
        "exp": (now + 86400) if exp is None else exp, "jti": secrets.token_hex(8),
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{h}.{p}.{_b64url(secrets.token_bytes(16))}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path, dict(self.headers)))
        if self.path == "/api/agent/asynx-config":
            self._send({"code": 0, "data": dict(self.server.asynx_config)})
        else:
            self._send({"code": 404}, 404)

    def do_POST(self) -> None:
        self.server.requests.append(("POST", self.path, dict(self.headers)))
        if self.path != "/api/agent/jwt":
            self._send({"code": 404}, 404)
            return
        auth = self.headers.get("Authorization") or ""
        sub = self.server.pats.get(auth[7:]) if auth.startswith("Bearer ") else None
        if sub is None:
            self._send({"error": "unauthorized"}, 401)
            return
        if not self.server.scope_ok:
            self._send({"error": "forbidden"}, 403)
            return
        cfg = self.server.asynx_config
        token = make_test_jwt(
            sub=sub, kid=cfg["kid"], issuer=cfg["issuer"], audience=cfg["audience"],
            exp=int(time.time()) + int(cfg["tokenExpiresIn"]),
        )
        self.server.issued.append(token)
        self._send({"code": 0, "data": {
            "token": token, "expiresIn": cfg["tokenExpiresIn"],
            "expiresAt": (int(time.time()) + int(cfg["tokenExpiresIn"])) * 1000,
        }})


class FakeSageInServer(HTTPServer):
    def __init__(self, gateway_base_url: str) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.issued: list[str] = []
        self.scope_ok = True
        # Accepted PATs -> account id (JWT sub). Rotation/revocation edits this map.
        self.pats: dict[str, str] = {PAT: "user_1"}
        self.asynx_config = {
            "baseUrl": gateway_base_url, "tokenHeader": "X-Asynx-Partner-Token",
            "issuer": "login_svr", "audience": "asynx", "kid": "k1", "tokenExpiresIn": 86400,
        }


def run_fake_sagein_server(
    gateway_base_url: str = "http://127.0.0.1:9999",
) -> tuple[FakeSageInServer, str, threading.Thread]:
    server = FakeSageInServer(gateway_base_url)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, origin, thread


BRIDGE_SCRIPT = Path(__file__).resolve().with_name("fake_jwt_bridge.py")


def write_binding(config_dir: Path, agent_id: str, account_id: str, pat: str, *, cache: bool = False) -> None:
    """Binding used by tests/fake_jwt_bridge.py (simplified stand-in for sagein-community's store)."""
    bindings = config_dir / "bindings"
    bindings.mkdir(parents=True, exist_ok=True)
    (bindings / f"{agent_id}.json").write_text(
        json.dumps({"account_id": account_id, "access_token": pat, "cache": cache}), encoding="utf-8"
    )


def install_fake_context(
    skill_dir: Path,
    origin: str,
    *,
    agent_id: str = "agent-a",
    config_dir: Path | None = None,
    account_id: str | None = None,
    credential_file: Path | None = None,
    bridge_script: Path | None = None,
    binding: bool = True,
) -> tuple[Path, Path]:
    """Write .plaiers-context.json the way the Plaiers installer does, pointing at the fake bridge.

    Returns (context_path, config_dir). The context holds only the bridge argv, never a PAT.
    """
    import sys

    config_dir = config_dir or skill_dir / "sagein-community"
    if binding:
        write_binding(config_dir, agent_id, account_id or "user_1", PAT)
    argv = [sys.executable, str(bridge_script or BRIDGE_SCRIPT), "--agent-id", agent_id, "--origin", origin,
            "--config-dir", str(config_dir)]
    if credential_file is not None:
        argv += ["--credential-file", str(credential_file)]
    if account_id is not None:
        argv += ["--account-id", account_id]
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / ".plaiers-context.json"
    path.write_text(json.dumps({
        "schema_version": 1, "origin": origin, "agent_id": agent_id, "jwt_command": argv,
    }, indent=1), encoding="utf-8")
    return path, config_dir
