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
    runtime = subparsers.add_parser(
        "runtime", help="管理所有 Asset Skill 共用的可选运行环境（Pillow），只在用户同意后准备"
    )
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_commands.add_parser("status", help="查看共享运行环境状态，不下载、不修改")
    runtime_commands.add_parser("prepare", help="按哈希锁下载并准备共享运行环境（会联网下载，需用户同意）")
    runtime_commands.add_parser("remove", help="删除当前平台的共享运行环境")


def handle_auth_command(args: argparse.Namespace) -> tuple[dict[str, Any], int] | None:
    if getattr(args, "command", None) == "runtime":
        from . import runtime_env

        if args.runtime_command == "prepare":
            return runtime_env.prepare_runtime(), 0
        if args.runtime_command == "remove":
            return runtime_env.remove_runtime(), 0
        from .optional_deps import optional_runtime_status

        return {"ok": True, "runtime": optional_runtime_status()}, 0
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
