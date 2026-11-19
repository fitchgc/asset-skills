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
    update = subparsers.add_parser(
        "update", help="向 Plaiers 技能库查询已装版本；Skill 不会自行改写文件"
    )
    update_commands = update.add_subparsers(dest="update_command", required=True)
    update_commands.add_parser("status", help="对比已装锁定版本与技能库当前版本，报告是否已撤回（联网，只读）")
    update_commands.add_parser("check", help="同 status（兼容旧命令）")
    update_commands.add_parser("apply", help="已移除：改用 Plaiers 安装器安装新的锁定版本")
    update_commands.add_parser("rollback", help="已移除：安装器替换时会备份旧副本")


def handle_auth_command(args: argparse.Namespace) -> tuple[dict[str, Any], int] | None:
    if getattr(args, "command", None) == "runtime":
        from . import runtime_env

        if args.runtime_command == "prepare":
            return runtime_env.prepare_runtime(), 0
        if args.runtime_command == "remove":
            return runtime_env.remove_runtime(), 0
        from .optional_deps import optional_runtime_status

        return {"ok": True, "runtime": optional_runtime_status()}, 0
    if getattr(args, "command", None) == "update":
        from .updates import update_apply_removed, update_status

        if args.update_command in {"status", "check"}:
            return update_status(), 0
        return update_apply_removed(), 2
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
