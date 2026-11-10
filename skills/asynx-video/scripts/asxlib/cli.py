from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Any, NoReturn

from .artifacts import recent_payload
from .batches import execute_batch
from .client import AsynxClient
from .config import config_status, configure, doctor, legacy_installation_notice, load_credentials
from .constants import DEFAULT_OUTPUT_DIR, KNOWN_STATUSES, VERSION
from .errors import AsxError
from .local_video import execute_local_video
from .output import emit, log
from .state import connect_db as connect_ledger_db, task_paths as ledger_task_paths
from .tasks import (
    cancel_local_task,
    history_payload,
    local_asset_payload,
    local_task_status,
    local_tasks_payload,
    models_payload,
    poll_local_tasks,
    recover_tasks,
    refresh_task,
    run_task_submission,
    task_payload,
    wait_and_download,
)
from .video_tasks import build_video_task


def _client() -> AsynxClient:
    api_key, base_url = load_credentials()
    return AsynxClient(base_url, api_key)


def _add_local_video_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    video = commands.add_parser("video", help="本地确定性视频处理，不调用 Asynx")
    video_commands = video.add_subparsers(dest="video_command", required=True)

    info = video_commands.add_parser("info", help="查看本地视频元数据")
    info.add_argument("input")

    extract = video_commands.add_parser("extract-frames", help="视频抽帧为序列图片")
    extract.add_argument("input")
    extract.add_argument("--output-dir", required=True)
    extract.add_argument("--fps", type=float, help="抽帧帧率，省略时按原始帧率抽取全部帧")
    extract.add_argument("--prefix", default="frame")

    sheet = video_commands.add_parser("spritesheet", help="视频抽帧并打包为 2D 序列帧图集 (Atlas)")
    sheet.add_argument("input")
    sheet.add_argument("--output-dir", required=True)
    sheet.add_argument("--fps", type=float, default=10.0, help="采样帧率")
    sheet.add_argument("--columns", type=int, help="网格列数，默认自动计算最接近正方形")
    sheet.add_argument("--width", type=int, help="单帧目标宽度")
    sheet.add_argument("--height", type=int, help="单帧目标高度")

    trim = video_commands.add_parser("trim", help="修剪视频片段")
    trim.add_argument("input")
    trim.add_argument("--output", required=True)
    trim.add_argument("--start", type=float, default=0.0)
    trim.add_argument("--end", type=float)
    trim.add_argument("--duration", type=float)

    convert = video_commands.add_parser("convert", help="转换视频格式 (mp4, webm, gif)")
    convert.add_argument("input")
    convert.add_argument("--output", required=True)
    convert.add_argument("--format", choices=("mp4", "webm", "gif", "mov", "mkv"))

    mux = video_commands.add_parser("mux", help="音视频合并封装")
    mux.add_argument("--video", required=True)
    mux.add_argument("--audio", required=True)
    mux.add_argument("--output", required=True)


def _add_video_generation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", help="视频画面与动态描述")
    parser.add_argument("--model", help="指定模型")
    parser.add_argument("--image", help="首帧参考图路径")
    parser.add_argument("--end-image", help="末帧参考图路径")
    parser.add_argument("--from-task", help="使用之前任务的图片或视频结果")
    parser.add_argument("--reference", help="运动或风格参考视频")
    parser.add_argument("--duration", type=float, help="时长（秒）")
    parser.add_argument("--aspect-ratio", help="宽高比（如 16:9, 9:16, 1:1）")
    parser.add_argument("--resolution", choices=("720p", "1080p", "4k"), help="分辨率档位")
    parser.add_argument("--fps", type=int, choices=(24, 30, 60), help="帧率")
    parser.add_argument("--generate-audio", action="store_true", help="同时生成同步音效/配乐")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--detach", action="store_true")


def parser() -> argparse.ArgumentParser:
    res = argparse.ArgumentParser(description="通过 Asynx 生成、编辑与处理视频 (文生视频、图生视频、视频序列帧)")
    res.add_argument("--version", action="version", version=VERSION)
    commands = res.add_subparsers(dest="command", required=True)

    _add_local_video_commands(commands)

    # Cloud generation
    gen = commands.add_parser("generate", help="文生视频或图生视频")
    _add_video_generation_options(gen)

    edit = commands.add_parser("edit", help="编辑或扩展已有视频")
    _add_video_generation_options(edit)
    edit.add_argument("--video", help="源视频文件路径")

    # UI workbenches
    ui = commands.add_parser("ui", help="启动本地视频视觉交互工作台")
    ui_commands = ui.add_subparsers(dest="ui_command", required=True)
    ui_fs = ui_commands.add_parser("frame-sequence", help="交互式视频时间轴抽帧、去背与图集导出工作台")
    ui_fs.add_argument("--video", help="输入视频路径")
    ui_fs.add_argument("--from-task", help="使用前序任务的视频")
    ui_fs.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    ui_fs.add_argument("--no-open", action="store_true")

    ui_prev = ui_commands.add_parser("preview-sequence", help="交互式预览已有序列帧图集")
    ui_prev.add_argument("--atlas", required=True, help="atlas.png 路径")
    ui_prev.add_argument("--manifest", required=True, help="manifest.json 路径")
    ui_prev.add_argument("--no-open", action="store_true")

    # Task lifecycle
    status = commands.add_parser("status", help="查询任务进度")
    status.add_argument("task_id")

    wait = commands.add_parser("wait", help="等待并下载任务产物")
    wait.add_argument("task_id")
    wait.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    history = commands.add_parser("history", help="查询远端历史任务")
    history.add_argument("--status", choices=tuple(sorted(KNOWN_STATUSES)))
    history.add_argument("--limit", type=int, default=20)

    recent = commands.add_parser("recent", help="查询本机最近下载的视频产物")
    recent.add_argument("--query")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--latest", action="store_true")
    recent.add_argument("--all-modalities", action="store_true")

    task = commands.add_parser("task", help="管理本地视频任务")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_list = task_commands.add_parser("list", help="列出本地任务")
    task_list.add_argument("--status")
    task_list.add_argument("--limit", type=int, default=50)
    task_list.add_argument("--all-modalities", action="store_true")
    task_status = task_commands.add_parser("status", help="查看任务快照")
    task_status.add_argument("task_id")
    task_status.add_argument("--refresh", action="store_true")
    task_poll = task_commands.add_parser("poll", help="推进刷新本地任务")
    task_poll.add_argument("task_id", nargs="?")
    task_poll.add_argument("--limit", type=int, default=50)
    task_recover = task_commands.add_parser("recover", help="恢复中断任务")
    task_recover.add_argument("task_id", nargs="?")
    task_recover.add_argument("--limit", type=int, default=50)
    task_cancel = task_commands.add_parser("cancel", help="取消任务")
    task_cancel.add_argument("task_id")

    asset = commands.add_parser("asset", help="查看本地产物")
    asset_commands = asset.add_subparsers(dest="asset_command", required=True)
    asset_list = asset_commands.add_parser("list", help="列出任务输出视频")
    asset_list.add_argument("task_id")

    batch = commands.add_parser("batch", help="管理视频批次")
    batch_commands = batch.add_subparsers(dest="batch_command", required=True)
    batch_commands.add_parser("list", help="列出视频批次")
    b_status = batch_commands.add_parser("status", help="查看批次状态")
    b_status.add_argument("batch_id", nargs="?")
    b_status.add_argument("--refresh", action="store_true")
    b_status.add_argument("--items", action="store_true")
    b_wait = batch_commands.add_parser("wait", help="等待批次完成")
    b_wait.add_argument("batch_id", nargs="?")
    b_wait.add_argument("--interval", type=float, default=10)
    b_wait.add_argument("--max-wait", type=float, default=0)
    b_pause = batch_commands.add_parser("pause", help="暂停批次")
    b_pause.add_argument("batch_id", nargs="?")
    b_resume = batch_commands.add_parser("resume", help="恢复批次")
    b_resume.add_argument("batch_id", nargs="?")
    b_cancel = batch_commands.add_parser("cancel", help="取消批次")
    b_cancel.add_argument("batch_id", nargs="?")

    doctor_cmd = commands.add_parser("doctor", help="诊断视频 Skill 环境配置")
    doctor_cmd.add_argument("--verify", action="store_true")
    doctor_cmd.add_argument("--updates", action="store_true")

    configure_cmd = commands.add_parser("configure", help="配置 Asynx API Key")
    configure_cmd.add_argument("--base-url")

    config_cmd = commands.add_parser("config", help="查看配置状态")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("status")

    models = commands.add_parser("models", help="列出可用视频模型")
    models.add_argument("--operation", choices=("all", "generate", "edit"), default="all")

    update = commands.add_parser("update", help="检查与更新 Skill")
    update_sub = update.add_subparsers(dest="update_command", required=True)
    update_sub.add_parser("status")
    update_check_cmd = update_sub.add_parser("check")
    update_check_cmd.add_argument("--force", action="store_true")
    update_apply_cmd = update_sub.add_parser("apply")
    update_apply_cmd.add_argument("--target", default="all")
    update_apply_cmd.add_argument("--force", action="store_true")
    update_sub.add_parser("rollback")

    return res


def execute(args: argparse.Namespace, *, locked_root: Path | None = None) -> tuple[dict[str, Any], int]:
    if args.command == "video":
        return execute_local_video(args)

    if args.command == "ui":
        from .ui_server import launch_workbench
        ui_static = Path(__file__).resolve().parent / "ui"
        if not ui_static.is_dir():
            ui_static = Path(__file__).resolve().parents[2] / "src" / "ui" / "video"

        video_path = getattr(args, "video", None)
        from_task = getattr(args, "from_task", None)
        if from_task and not video_path:
            conn = connect_ledger_db()
            try:
                paths = ledger_task_paths(conn, from_task)
                video_path = paths[0] if paths else None
            finally:
                conn.close()

        open_browser = not getattr(args, "no_open", False)
        if args.ui_command == "frame-sequence":
            return launch_workbench(
                mode="video_frame_sequence",
                source_path=video_path,
                output_dir=args.output_dir,
                static_dir=ui_static,
                client=_client(),
                open_browser=open_browser,
            )
        if args.ui_command == "preview-sequence":
            return launch_workbench(
                mode="sequence_preview",
                source_path=args.atlas,
                metadata={"manifest_path": args.manifest},
                static_dir=ui_static,
                client=_client(),
                open_browser=open_browser,
            )

    if args.command == "configure":
        return configure(args.base_url), 0
    if args.command == "config" and args.config_command == "status":
        return config_status(), 0
    if args.command == "doctor":
        payload = doctor(verify=args.verify, modality="video")
        if args.updates:
            from .updates import update_check
            payload["updates"] = update_check()
        return payload, 0 if payload["ok"] else 2

    if args.command == "update":
        from .updates import update_apply, update_check, update_rollback, update_status
        if args.update_command == "status":
            return update_status(), 0
        if args.update_command == "check":
            checked = update_check(force=args.force)
            return checked, 0 if checked["ok"] else 2
        if args.update_command == "apply":
            return update_apply(target=args.target, force=args.force, already_locked=locked_root), 0
        return update_rollback(already_locked=locked_root), 0

    if args.command == "recent":
        modality = None if getattr(args, "all_modalities", False) else "video"
        return recent_payload(args.query, args.limit, latest=args.latest, modality=modality), 0

    if args.command == "task":
        modality = None if getattr(args, "all_modalities", False) else "video"
        if args.task_command == "list":
            return local_tasks_payload(modality=modality, status=args.status, limit=args.limit), 0
        if args.task_command == "status":
            if args.refresh:
                result, code = refresh_task(_client(), args.task_id)
                return {"ok": result["ok"], "schema_version": 2, "task": result}, code
            return local_task_status(args.task_id), 0
        client = _client()
        if args.task_command == "poll":
            return poll_local_tasks(client, args.task_id, limit=args.limit, modality=modality), 0
        if args.task_command == "recover":
            result = recover_tasks(client, args.task_id, limit=args.limit, modality=modality)
            return result, 0 if result["ok"] else 4
        if args.task_command == "cancel":
            return cancel_local_task(client, args.task_id)

    if args.command == "asset" and args.asset_command == "list":
        return local_asset_payload(args.task_id), 0

    if args.command == "batch":
        setattr(args, "modality", "video")
        client = _client() if getattr(args, "refresh", False) or args.batch_command in {"wait", "poll", "cancel"} else None
        return execute_batch(client, args)

    client = _client()
    if args.command == "models":
        return models_payload(client, args.operation, modality="video"), 0

    if args.command == "status":
        task, request_id = client.task(args.task_id)
        return task_payload(task, request_id, modality="video"), 0

    if args.command == "wait":
        return wait_and_download(client, args.task_id, args.output_dir)

    if args.command == "history":
        return history_payload(client, status=args.status, limit=args.limit), 0

    if args.command in {"generate", "edit"}:
        task_type = f"video.{args.command}"
        image_path = getattr(args, "image", None)
        from_task = getattr(args, "from_task", None)
        if from_task and not image_path:
            conn = connect_ledger_db()
            try:
                paths = ledger_task_paths(conn, from_task)
                image_path = paths[0] if paths else None
            finally:
                conn.close()

        body, selected_model = build_video_task(
            client,
            task_type=task_type,
            model_selector=getattr(args, "model", None),
            prompt=getattr(args, "prompt", None),
            image=image_path,
            end_image=getattr(args, "end_image", None),
            video=getattr(args, "video", None),
            reference=getattr(args, "reference", None),
            duration=getattr(args, "duration", None),
            aspect_ratio=getattr(args, "aspect_ratio", None),
            resolution=getattr(args, "resolution", None),
            fps=getattr(args, "fps", None),
            generate_audio=getattr(args, "generate_audio", None),
        )
        return run_task_submission(
            client,
            task_type=task_type,
            body=body,
            model=selected_model,
            output_dir=args.output_dir,
            idempotency_key=args.idempotency_key or f"asx-video-{args.command}-{secrets.token_hex(8)}",
            detach=args.detach,
            modality="video",
        )

    raise AssertionError("unreachable")


def main(*, locked_root: Path | None = None) -> NoReturn:
    args: argparse.Namespace | None = None
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
        payload, exit_code = AsxError(f"操作失败：{exc}", code="unexpected_error", exit_code=2).payload(), 2

    legacy_notice = legacy_installation_notice()
    if legacy_notice:
        payload["legacy_installation_notice"] = legacy_notice

    emit(payload)
    raise SystemExit(exit_code)
