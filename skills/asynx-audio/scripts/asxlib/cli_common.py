from __future__ import annotations

import argparse
import os
from typing import Any

from .auth import clear_auth_session, load_auth_session
from .client import AsynxClient
from .ui_server import configure_login_ui, launch_login_workbench_session


def add_auth_commands(
    root_parser: argparse.ArgumentParser,
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    root_parser.add_argument(
        "--no-login-ui",
        action="store_true",
        default=False,
        help="禁用自动启动登录工作台，未登录时直接返回 need_login",
    )

    login_cmd = subparsers.add_parser("login", help="打开本地登录工作台登录或注册 Asynx 账号")
    login_cmd.add_argument("--login-svr-url", help="指定 Identity Service 地址")
    login_cmd.add_argument("--no-open", action="store_true", help="不自动打开桌面浏览器")

    subparsers.add_parser("logout", help="清除本地保存的登录令牌")
    subparsers.add_parser("whoami", help="查看当前登录用户与 Asynx 额度/并发状态")


def attach_global_auth_flags(root_parser: argparse.ArgumentParser) -> None:
    """Allow `--no-login-ui` to be passed either before or after subcommands."""

    def _walk(p: argparse.ArgumentParser) -> None:
        if "--no-login-ui" not in p._option_string_actions:
            p.add_argument(
                "--no-login-ui",
                action="store_true",
                default=argparse.SUPPRESS,
                help=argparse.SUPPRESS,
            )
        if p._subparsers:
            for action in p._subparsers._group_actions:
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        _walk(child)

    if root_parser._subparsers:
        for action in root_parser._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    _walk(child)


def prepare_cli_auth(args: argparse.Namespace) -> None:
    configure_login_ui(no_login_ui=bool(getattr(args, "no_login_ui", False)))


def handle_auth_command(args: argparse.Namespace) -> tuple[dict[str, Any], int] | None:
    cmd = getattr(args, "command", None)
    if cmd == "login":
        no_open = bool(getattr(args, "no_open", False)) or (
            os.environ.get("ASYNX_NO_OPEN_BROWSER", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        payload, exit_code, _auth_res = launch_login_workbench_session(
            login_svr_url=getattr(args, "login_svr_url", None),
            open_browser=not no_open,
        )
        return payload, exit_code

    if cmd == "logout":
        clear_auth_session(keep_login_svr_url=True)
        return {"ok": True, "logged_out": True}, 0

    if cmd == "whoami":
        session = load_auth_session()
        client = AsynxClient(session.base_url, session=session)
        identity, request_id = client.partner_identity()
        return {
            "ok": True,
            "authenticated": True,
            "login_svr_url": session.login_svr_url,
            "base_url": session.base_url,
            "user": session.user,
            "identity": identity,
            "request_id": request_id,
        }, 0

    return None
