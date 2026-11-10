from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Any, NoReturn

from .artifacts import recent_payload
from .audio_tasks import build_audio_task
from .batches import execute_batch
from .client import AsynxClient
from .config import config_status, configure, doctor, legacy_installation_notice, load_credentials
from .constants import DEFAULT_OUTPUT_DIR, KNOWN_STATUSES, VERSION
from .errors import AsxError
from .local_audio import execute_local_audio
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


def _client() -> AsynxClient:
    api_key, base_url = load_credentials()
    return AsynxClient(base_url, api_key)


def _add_local_audio_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    audio = commands.add_parser("audio", help="本地确定性音频处理，不调用 Asynx")
    audio_commands = audio.add_subparsers(dest="audio_command", required=True)

    info = audio_commands.add_parser("info", help="查看本地音频信息")
    info.add_argument("input")

    convert = audio_commands.add_parser("convert", help="转换音频格式")
    convert.add_argument("input")
    convert.add_argument("--output", required=True)
    convert.add_argument("--format", choices=("wav", "mp3", "ogg", "m4a", "flac"))

    trim = audio_commands.add_parser("trim", help="修剪音频片段")
    trim.add_argument("input")
    trim.add_argument("--output", required=True)
    trim.add_argument("--start", type=float, default=0.0)
    trim.add_argument("--end", type=float)
    trim.add_argument("--duration", type=float)

    fade = audio_commands.add_parser("fade", help="应用淡入淡出包络")
    fade.add_argument("input")
    fade.add_argument("--output", required=True)
    fade.add_argument("--fade-in", type=float, default=0.0)
    fade.add_argument("--fade-out", type=float, default=0.0)

    norm = audio_commands.add_parser("normalize", help="音频响度标准化")
    norm.add_argument("input")
    norm.add_argument("--output", required=True)
    norm.add_argument("--target-lufs", type=float, default=-14.0)

    concat = audio_commands.add_parser("concat", help="拼接多个音频文件")
    concat.add_argument("inputs", nargs="+")
    concat.add_argument("--output", required=True)


def parser() -> argparse.ArgumentParser:
    res = argparse.ArgumentParser(description="通过 Asynx 生成、合成与处理音频 (音效、音乐、语音)")
    res.add_argument("--version", action="version", version=VERSION)
    commands = res.add_subparsers(dest="command", required=True)

    _add_local_audio_commands(commands)

    # Cloud generation commands
    sfx = commands.add_parser("sfx", help="生成音效素材")
    sfx.add_argument("--prompt", required=True, help="音效描述，如 UI 点击、脚步声、挥剑音效")
    sfx.add_argument("--model", help="指定模型")
    sfx.add_argument("--duration", type=float, help="时长（秒）")
    sfx.add_argument("--output-format", choices=("wav", "mp3", "ogg"), default="mp3")
    sfx.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    sfx.add_argument("--idempotency-key")
    sfx.add_argument("--detach", action="store_true")

    music = commands.add_parser("music", help="生成背景音乐与配乐")
    music.add_argument("--prompt", required=True, help="音乐风格、乐器与情绪描述")
    music.add_argument("--model", help="指定模型")
    music.add_argument("--lyrics", help="歌词文本")
    music.add_argument("--instrumental", action="store_true", help="纯器乐，无人声")
    music.add_argument("--duration", type=float, help="时长（秒）")
    music.add_argument("--output-format", choices=("wav", "mp3", "ogg"), default="mp3")
    music.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    music.add_argument("--idempotency-key")
    music.add_argument("--detach", action="store_true")

    speech = commands.add_parser("speech", help="合成角色台词与语音")
    speech.add_argument("--text", required=True, help="朗读文本")
    speech.add_argument("--voice", help="音色名称或编号")
    speech.add_argument("--reference-audio", help="参考音色音频文件路径")
    speech.add_argument("--from-task", help="使用之前任务产出的音频作为参考音色")
    speech.add_argument("--speed", type=float, default=1.0, help="语速倍率")
    speech.add_argument("--emotion", help="情绪（如 happy, angry, calm）")
    speech.add_argument("--model", help="指定模型")
    speech.add_argument("--output-format", choices=("wav", "mp3", "ogg"), default="mp3")
    speech.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    speech.add_argument("--idempotency-key")
    speech.add_argument("--detach", action="store_true")

    # Common task lifecycle commands
    status = commands.add_parser("status", help="查询任务进度")
    status.add_argument("task_id")

    wait = commands.add_parser("wait", help="等待并下载任务产物")
    wait.add_argument("task_id")
    wait.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    history = commands.add_parser("history", help="查询远端历史任务")
    history.add_argument("--status", choices=tuple(sorted(KNOWN_STATUSES)))
    history.add_argument("--limit", type=int, default=20)

    recent = commands.add_parser("recent", help="查询本机最近下载的音频产物")
    recent.add_argument("--query", help="关键词筛选")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--latest", action="store_true")
    recent.add_argument("--all-modalities", action="store_true")

    task = commands.add_parser("task", help="管理本地音频任务")
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
    asset_list = asset_commands.add_parser("list", help="列出任务输出音频")
    asset_list.add_argument("task_id")

    # Batches
    batch = commands.add_parser("batch", help="管理音频批次")
    batch_commands = batch.add_subparsers(dest="batch_command", required=True)
    batch_commands.add_parser("list", help="列出音频批次")
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

    # Setup & Doctor
    doctor_cmd = commands.add_parser("doctor", help="诊断音频 Skill 环境配置")
    doctor_cmd.add_argument("--verify", action="store_true")
    doctor_cmd.add_argument("--updates", action="store_true")

    configure_cmd = commands.add_parser("configure", help="配置 Asynx API Key")
    configure_cmd.add_argument("--base-url")

    config_cmd = commands.add_parser("config", help="查看配置状态")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("status")

    models = commands.add_parser("models", help="列出可用音频模型")
    models.add_argument("--operation", choices=("all", "sfx", "music", "speech"), default="all")

    # Updates
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
    if args.command == "audio":
        return execute_local_audio(args)

    if args.command == "configure":
        return configure(args.base_url), 0
    if args.command == "config" and args.config_command == "status":
        return config_status(), 0
    if args.command == "doctor":
        payload = doctor(verify=args.verify, modality="audio")
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
        modality = None if getattr(args, "all_modalities", False) else "audio"
        return recent_payload(args.query, args.limit, latest=args.latest, modality=modality), 0

    if args.command == "task":
        modality = None if getattr(args, "all_modalities", False) else "audio"
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
        setattr(args, "modality", "audio")
        client = _client() if getattr(args, "refresh", False) or args.batch_command in {"wait", "poll", "cancel"} else None
        return execute_batch(client, args)

    client = _client()
    if args.command == "models":
        return models_payload(client, args.operation, modality="audio"), 0

    if args.command == "status":
        task, request_id = client.task(args.task_id)
        return task_payload(task, request_id, modality="audio"), 0

    if args.command == "wait":
        return wait_and_download(client, args.task_id, args.output_dir)

    if args.command == "history":
        return history_payload(client, status=args.status, limit=args.limit), 0

    # Submission commands: sfx, music, speech
    if args.command in {"sfx", "music", "speech"}:
        task_type = f"audio.{args.command}"
        ref_audio = getattr(args, "reference_audio", None)
        from_task = getattr(args, "from_task", None)
        if from_task:
            conn = connect_ledger_db()
            try:
                paths = ledger_task_paths(conn, from_task)
                ref_audio = paths[0] if paths else None
            finally:
                conn.close()

        body, selected_model = build_audio_task(
            client,
            task_type=task_type,
            model_selector=getattr(args, "model", None),
            prompt=getattr(args, "prompt", None),
            text=getattr(args, "text", None),
            duration=getattr(args, "duration", None),
            lyrics=getattr(args, "lyrics", None),
            instrumental=getattr(args, "instrumental", None),
            voice=getattr(args, "voice", None),
            reference_audio=ref_audio,
            speed=getattr(args, "speed", None),
            emotion=getattr(args, "emotion", None),
            output_format=getattr(args, "output_format", None),
        )
        return run_task_submission(
            client,
            task_type=task_type,
            body=body,
            model=selected_model,
            output_dir=args.output_dir,
            idempotency_key=args.idempotency_key or f"asx-audio-{args.command}-{secrets.token_hex(8)}",
            detach=args.detach,
            modality="audio",
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
