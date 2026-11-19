from __future__ import annotations

import argparse
from typing import Any

from .auth import load_auth_session
from .client import AsynxClient


def add_auth_commands(
    root_parser: argparse.ArgumentParser,
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    subparsers.add_parser("whoami", help="查看当前 Asynx 额度/并发状态（通过 SageIn Agent 授权换取 JWT）")


def handle_auth_command(args: argparse.Namespace) -> tuple[dict[str, Any], int] | None:
    if getattr(args, "command", None) == "whoami":
        session = load_auth_session()
        client = AsynxClient(session.base_url, session=session)
        identity, request_id = client.partner_identity()
        return {
            "ok": True,
            "authenticated": True,
            "origin": session.origin,
            "base_url": session.base_url,
            "identity": identity,
            "request_id": request_id,
        }, 0
    return None
