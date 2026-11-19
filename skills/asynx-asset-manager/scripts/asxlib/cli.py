from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, NoReturn

from .cli_common import (
    add_auth_commands,
    handle_auth_command,
)
from .config import config_status, doctor, legacy_installation_notice
from .constants import VERSION
from .errors import AsxError
from .output import emit
from .scanner import ASSET_CATEGORIES, asset_lineage_report, scan_workspace


def _add_scan_arguments(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--root", default=".", help="要扫描的项目工作区根目录（默认为当前目录）")
    cmd.add_argument(
        "--category",
        choices=("all", *ASSET_CATEGORIES),
        default="all",
        help="按五类资产过滤：image, audio, frame_sequence, video, 3d 或 all",
    )
    cmd.add_argument(
        "--managed-only",
        action="store_true",
        help="仅显示由 Asynx Asset Skill 生成或修改的受管资产",
    )
    cmd.add_argument(
        "--external-only",
        action="store_true",
        help="仅显示项目中的外部未受管资产",
    )
    cmd.add_argument(
        "--source",
        choices=("all", "managed", "external"),
        default="all",
        help="按来源过滤：all, managed 或 external",
    )
    cmd.add_argument("--query", help="按名称、路径、提示词、模型、操作或 ID 搜索")
    cmd.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用 SHA-256 增量扫描缓存，强制重新计算摘要",
    )


def parser() -> argparse.ArgumentParser:
    res = argparse.ArgumentParser(
        description="Asynx 统一资产管理器：扫描五类创作资产、识别 Skill 产物、SHA-256 移动重连与血缘追踪"
    )
    res.add_argument("--version", action="version", version=VERSION)
    commands = res.add_subparsers(dest="command", required=True)

    add_auth_commands(res, commands)

    scan_cmd = commands.add_parser("scan", help="扫描项目工作区资产并自动重连已移动/重命名文件")
    _add_scan_arguments(scan_cmd)

    list_cmd = commands.add_parser("list", help="scan 的别名：列出工作区分类资产清单")
    _add_scan_arguments(list_cmd)

    lineage_cmd = commands.add_parser("lineage", help="查询指定资产的上下游 Asset Lineage（血缘关系）")
    lineage_cmd.add_argument("target", help="目标资产的 artifact_id 或文件路径")
    lineage_cmd.add_argument("--root", help="可选工作区根目录，用于解析相对路径")

    ui_cmd = commands.add_parser("ui", help="启动统一资产管理器 Local UI Workbench")
    ui_cmd.add_argument("--root", default=".", help="项目工作区根目录（默认为当前目录）")
    ui_cmd.add_argument("--no-open", action="store_true", help="不自动打开桌面浏览器")

    doctor_cmd = commands.add_parser("doctor", help="诊断 Asset Manager 环境与认证配置")
    doctor_cmd.add_argument("--verify", action="store_true")
    doctor_cmd.add_argument("--updates", action="store_true")


    config_cmd = commands.add_parser("config", help="查看配置状态")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("status")


    return res


def execute(args: argparse.Namespace, *, locked_root: Path | None = None) -> tuple[dict[str, Any], int]:
    auth_result = handle_auth_command(args)
    if auth_result is not None:
        return auth_result

    if args.command in {"scan", "list"}:
        payload = scan_workspace(
            args.root,
            category=args.category,
            managed_only=bool(args.managed_only),
            external_only=bool(args.external_only),
            source=args.source,
            query=args.query,
            use_cache=not bool(args.no_cache),
        )
        return payload, 0

    if args.command == "lineage":
        return asset_lineage_report(args.target, root=args.root), 0

    if args.command == "ui":
        from .ui_server import launch_workbench

        ui_static = Path(__file__).resolve().parent / "ui"
        if not ui_static.is_dir():
            ui_static = Path(__file__).resolve().parents[2] / "src" / "ui" / "asset_manager"
        workspace_root = str(Path(args.root).expanduser().resolve())
        return launch_workbench(
            mode="asset_manager",
            output_dir=str(Path(workspace_root) / "generated-assets"),
            metadata={"workspace_root": workspace_root},
            static_dir=ui_static,
            client=None,
            open_browser=not getattr(args, "no_open", False),
        )

    if args.command == "config" and args.config_command == "status":
        return config_status(), 0
    if args.command == "doctor":
        payload = doctor(verify=args.verify)
        if args.updates:
            from .updates import update_status_for_doctor
            payload["updates"] = update_status_for_doctor()
        return payload, 0 if payload["ok"] else 2


    raise AssertionError("unreachable")


def main(*, locked_root: Path | None = None) -> NoReturn:
    try:
        args = parser().parse_args()
        payload, exit_code = execute(args, locked_root=locked_root)
    except AsxError as exc:
        payload, exit_code = exc.payload(), exc.exit_code
    except KeyboardInterrupt:
        payload, exit_code = {
            "ok": False,
            "error": {"code": "interrupted", "message": "Operation interrupted"},
        }, 130
    except Exception as exc:
        payload, exit_code = (
            AsxError(f"操作失败：{exc}", code="unexpected_error", exit_code=2).payload(),
            2,
        )

    legacy_notice = legacy_installation_notice()
    if legacy_notice:
        payload["legacy_installation_notice"] = legacy_notice

    emit(payload)
    raise SystemExit(exit_code)
