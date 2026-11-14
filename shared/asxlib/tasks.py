from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .client import AsynxClient
from .config import initial_poll_delay_seconds
from .constants import (
    DEFAULT_OUTPUT_DIR,
    DOWNLOAD_LIMIT_BY_MODALITY,
    DOWNLOAD_TIMEOUT_BY_MODALITY,
    KNOWN_STATUSES,
    MAX_TASK_POLL_DELAY_SECONDS,
    TERMINAL_STATUSES,
    UTC,
)
from .errors import AsxError
from .output import log
from .state import (
    SCHEMA_VERSION,
    append_event,
    bind_remote_task,
    cancel_blocked_batch_intent,
    create_task_intent,
    list_assets,
    record_artifact_with_lineage,
    update_task,
    upsert_asset,
    utc_now,
)
from .state import connect_db as connect_ledger_db
from .state import get_task as get_ledger_task
from .state import list_tasks as list_ledger_tasks
from .state import task_paths as ledger_task_paths

_SAFE_PATH_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


class TaskStatusClient(Protocol):
    def task(self, task_id: str) -> tuple[dict[str, Any], str | None]: ...


def parse_deadline(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def wait_for_terminal(
    client: TaskStatusClient,
    task: dict[str, Any],
    request_id: str | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    initial_delay: float = MAX_TASK_POLL_DELAY_SECONDS,
) -> tuple[dict[str, Any], str | None]:
    task_id = task.get("id")
    if not isinstance(task_id, str):
        raise AsxError("Task response is missing an ID", code="invalid_task_response")
    deadline = parse_deadline(task.get("deadline_at"))
    client_deadline = (
        deadline + timedelta(seconds=30)
        if deadline
        else datetime.now(UTC) + timedelta(minutes=20)
    )
    delay = initial_delay
    last_status: str | None = None
    while True:
        status = task.get("status")
        if not isinstance(status, str) or status not in KNOWN_STATUSES:
            raise AsxError(
                f"Task {task_id} 返回了未知状态",
                code="invalid_task_status",
                task_id=task_id,
            )
        if status != last_status:
            log(f"Task {task_id}：{status}")
            last_status = status
        if status in TERMINAL_STATUSES:
            return task, request_id
        if datetime.now(UTC) >= client_deadline:
            raise AsxError(
                f"Stopped waiting for Task {task_id} after its execution deadline",
                code="client_wait_timeout",
                exit_code=4,
                task_id=task_id,
                request_id=request_id,
                details={"next_action_args": ["wait", task_id]},
            )
        sleep(delay)
        delay = MAX_TASK_POLL_DELAY_SECONDS + random.uniform(0.0, MAX_TASK_POLL_DELAY_SECONDS * 0.15)
        try:
            task, request_id = client.task(task_id)
        except AsxError as exc:
            exc.task_id = task_id
            if exc.details is None:
                exc.details = {}
            if isinstance(exc.details, dict):
                exc.details.setdefault("next_action_args", ["wait", task_id])
            raise


def guess_extension_for_mime(mime: str | None, default: str = "png") -> str:
    if not mime:
        return default
    normalized = mime.split(";", 1)[0].strip().lower()
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/m4a": "m4a",
        "audio/x-m4a": "m4a",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "model/gltf-binary": "glb",
        "model/gltf+json": "gltf",
        "model/obj": "obj",
        "application/zip": "zip",
        "application/json": "json",
    }
    return mapping.get(normalized, default)


def _unique_output_path(directory: Path, base_name: str, extension: str) -> Path:
    safe_name = _SAFE_PATH_COMPONENT.sub("_", base_name).strip("._") or "asset"
    return directory / f"{safe_name}.{extension}"


def _stored_lineage_context(
    connection: sqlite3.Connection, local_task_id: str
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT payload_json FROM task_events "
        "WHERE local_task_id = ? AND event_type = 'lineage_parents' "
        "ORDER BY id DESC LIMIT 1",
        (local_task_id,),
    ).fetchone()
    if row is None or not row["payload_json"]:
        return {}
    try:
        parsed = json.loads(str(row["payload_json"]))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def download_assets(
    client: AsynxClient,
    task: dict[str, Any],
    output_dir: str,
    *,
    prefix: str | None = None,
    metrics: list[dict[str, Any]] | None = None,
    local_task_id: str | None = None,
    completed_files: list[str] | None = None,
    modality: str | None = None,
    parent_paths: Sequence[str | Path] | None = None,
    operation: str | None = None,
    category: str | None = None,
    source_kind: str = "cloud_task",
    metadata: Mapping[str, Any] | None = None,
) -> list[str]:
    task_id = task.get("id")
    result = task.get("result")
    assets = result.get("assets") if isinstance(result, dict) else None
    if not isinstance(task_id, str) or not isinstance(assets, list) or not assets:
        raise AsxError(
            "Succeeded Task did not contain downloadable Assets",
            code="missing_task_assets",
            task_id=task_id if isinstance(task_id, str) else None,
        )
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files: list[str] = completed_files if completed_files is not None else []
    cached_assets: dict[int, dict[str, Any]] = {}
    task_row: dict[str, Any] | None = None
    resolved_parents = list(parent_paths) if parent_paths is not None else None
    resolved_metadata = dict(metadata) if metadata else None
    if local_task_id:
        cache_connection = connect_ledger_db()
        try:
            task_row = get_ledger_task(cache_connection, local_task_id=local_task_id)
            cached_assets = {
                int(item["remote_index"]): item
                for item in list_assets(cache_connection, local_task_id)
            }
            if resolved_parents is None or resolved_metadata is None:
                ctx = _stored_lineage_context(cache_connection, local_task_id)
                if resolved_parents is None and isinstance(ctx.get("parent_paths"), list):
                    resolved_parents = ctx["parent_paths"]
                if resolved_metadata is None and isinstance(ctx.get("metadata"), dict):
                    resolved_metadata = ctx["metadata"]
        finally:
            cache_connection.close()

    # Determine default extension and modality from task type or modality
    default_ext = "png"
    task_type = task.get("task_type", "")
    resolved_modality = modality or (
        str(task_row["modality"]) if task_row and task_row.get("modality") else None
    )
    if not resolved_modality and isinstance(task_type, str) and "." in task_type:
        resolved_modality = task_type.split(".", 1)[0]
    resolved_modality = resolved_modality or "image"
    resolved_operation = (
        operation
        or (str(task_row["operation"]) if task_row and task_row.get("operation") else None)
        or (task_type.split(".", 1)[-1] if isinstance(task_type, str) and "." in task_type else "generate")
    )
    if task_type.startswith("audio.") or resolved_modality == "audio":
        default_ext = "mp3"
    elif task_type.startswith("video.") or resolved_modality == "video":
        default_ext = "mp4"
    elif task_type.startswith("3d.") or resolved_modality == "3d":
        default_ext = "glb"

    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("index"), int):
            raise AsxError(
                "Task contains an invalid Asset",
                code="invalid_task_asset",
                task_id=task_id,
            )
        index = asset["index"]
        asset_metrics: dict[str, Any] = {}
        download_url = asset.get("download_url") if isinstance(asset.get("download_url"), str) else None
        cached = cached_assets.get(index)
        cached_path = cached.get("path") if cached else None
        if isinstance(cached_path, str) and cached_path and Path(cached_path).is_file():
            files.append(cached_path)
            asset_metrics.update(
                {
                    "source": "local_cache",
                    "bytes": Path(cached_path).stat().st_size,
                    "save_seconds": 0.0,
                }
            )
            if metrics is not None:
                metrics.append(asset_metrics)
            continue

        mime = asset.get("mime") or asset.get("content_type")
        ext = guess_extension_for_mime(mime, default=default_ext)
        dest_path = (
            Path(cached_path)
            if isinstance(cached_path, str) and cached_path
            else _unique_output_path(directory, f"{prefix or task_id}-{index}", ext)
        )

        dl_result = client.download_asset_to_file(
            task_id,
            index,
            dest_path,
            download_url=download_url,
            modality=resolved_modality,
            metrics=asset_metrics,
        )
        final_path = dl_result["path"]
        actual_mime = dl_result.get("content_type") or mime
        sha256_digest = dl_result["sha256"]
        byte_count = dl_result["bytes"]

        if metrics is not None:
            metrics.append(asset_metrics)

        if local_task_id:
            connection = connect_ledger_db()
            try:
                record_artifact_with_lineage(
                    connection,
                    file_path=final_path,
                    modality=resolved_modality,
                    operation=resolved_operation,
                    source_kind=source_kind,
                    category=category or resolved_modality,
                    local_task_id=local_task_id,
                    remote_index=index,
                    remote_url=download_url,
                    mime=actual_mime,
                    size_bytes=byte_count,
                    sha256=sha256_digest,
                    metadata=resolved_metadata,
                    parent_paths=resolved_parents,
                    task_id=task_id,
                )
            finally:
                connection.close()
        files.append(final_path)

    return files


def index_downloaded_artifacts(
    task: dict[str, Any],
    files: list[str],
    *,
    operation: str | None = None,
    modality: str | None = None,
    parent_paths: Sequence[str | Path] | None = None,
    category: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    remote_id = task.get("id")
    if not isinstance(remote_id, str):
        return {"code": "artifact_index_failed", "message": "结果缺少 Task ID，无法建立本地索引"}
    connection = connect_ledger_db()
    try:
        existing = get_ledger_task(connection, remote_task_id=remote_id)
        task_type = task.get("task_type")
        resolved_modality = modality or (
            task_type.split(".", 1)[0]
            if isinstance(task_type, str) and "." in task_type
            else (existing.get("modality") if existing else "image")
        ) or "image"
        operation_value = operation or (
            task_type.split(".", 1)[-1]
            if isinstance(task_type, str) and "." in task_type
            else "generate"
        )
        if existing is None:
            request: dict[str, Any] = {
                "task_type": task_type or f"{resolved_modality}.{operation_value}",
                "model": task.get("model") or "unknown",
                "input": task.get("input") if isinstance(task.get("input"), dict) else {},
            }
            key = f"asx-recovered-{hashlib.sha256(remote_id.encode()).hexdigest()[:24]}"
            existing = create_task_intent(
                connection,
                operation=operation_value,
                model=str(task.get("model") or "unknown"),
                idempotency_key=key,
                request=request,
                output_dir=str(Path(files[0]).parent) if files else DEFAULT_OUTPUT_DIR,
                status="succeeded",
                modality=resolved_modality,
            )
            bind_remote_task(
                connection,
                str(existing["local_id"]),
                remote_id,
                remote=task,
                status="succeeded",
                last_polled=utc_now(),
            )
        local_id = str(existing["local_id"])
        for index, path in enumerate(files):
            record_artifact_with_lineage(
                connection,
                file_path=path,
                modality=resolved_modality,
                operation=operation_value,
                source_kind="cloud_task",
                category=category or resolved_modality,
                local_task_id=local_id,
                remote_index=index,
                metadata=metadata,
                parent_paths=parent_paths,
                task_id=remote_id,
            )
        connection.commit()
    except (OSError, sqlite3.Error, AsxError) as exc:
        return {
            "code": "artifact_index_failed",
            "message": f"本地结果索引写入失败，资源仍已保存：{exc}",
        }
    finally:
        connection.close()
    return None


def _task_identifier_row(connection: sqlite3.Connection, identifier: str) -> dict[str, Any]:
    row = get_ledger_task(connection, local_task_id=identifier)
    if row is None:
        row = get_ledger_task(connection, remote_task_id=identifier)
    if row is None:
        raise AsxError(f"本地 Task 不存在：{identifier}", code="task_not_found")
    return row


def _ensure_partner_quota(client: Any) -> None:
    partner_fn = getattr(client, "partner_identity", None)
    if not callable(partner_fn):
        return
    identity, _req_id = partner_fn()
    if isinstance(identity, dict):
        api_key_info = identity.get("api_key")
        if isinstance(api_key_info, dict) and api_key_info.get("status") == "quota_exhausted":
            raise AsxError(
                "Asynx 账户额度已用尽 (quota_exhausted)，无法提交新任务。",
                code="quota_exhausted",
                exit_code=2,
                details={"partner_identity": identity},
            )


def _local_task_payload(row: dict[str, Any]) -> dict[str, Any]:
    remote = row.get("remote")
    payload: dict[str, Any] = {
        "ok": row.get("status") not in {"failed", "timeout", "canceled", "blocked", "orphaned"},
        "schema_version": SCHEMA_VERSION,
        "local_id": row.get("local_id"),
        "task_id": row.get("remote_task_id"),
        "status": row.get("status"),
        "model": row.get("model"),
        "operation": row.get("operation"),
        "modality": row.get("modality", "image"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_polled": row.get("last_polled"),
        "billing": row.get("billing"),
        "error": row.get("error"),
    }
    if isinstance(remote, dict):
        for key in ("result_quality", "result", "deadline_at", "started_at", "finished_at"):
            if remote.get(key) is not None:
                payload[key] = remote[key]
    _task_followup(payload)
    return {key: value for key, value in payload.items() if value is not None}


def _task_followup(payload: dict[str, Any]) -> None:
    identifier = payload.get("local_id") or payload.get("task_id")
    status = payload.get("status")
    if not identifier or status in {"failed", "timeout", "canceled", "blocked", "orphaned"}:
        return
    if status in {"intent", "submitting", "unknown"} and not payload.get("task_id"):
        payload["next_action"] = "task recover"
        payload["next_action_args"] = ["task", "recover", identifier]
    else:
        payload["next_action"] = "wait"
        payload["next_action_args"] = ["wait", identifier]


def _remote_task_for_row(client: AsynxClient, row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    remote_id = row.get("remote_task_id")
    if not isinstance(remote_id, str):
        raise AsxError(
            "该任务的提交尚未确认；恢复时请使用 task recover 并指定此任务 ID，不要重新生成。",
            code="task_not_submitted",
            task_id=str(row.get("local_id")),
            details={"next_action_args": ["task", "recover", str(row["local_id"])]},
        )
    return client.task(remote_id)


def sync_local_task(
    client: AsynxClient,
    identifier: str,
    *,
    download: bool = True,
    output_dir: str | None = None,
) -> tuple[dict[str, Any], int]:
    connection = connect_ledger_db()
    try:
        row = _task_identifier_row(connection, identifier)
    finally:
        connection.close()
    task, request_id = _remote_task_for_row(client, row)
    status = task.get("status")
    if not isinstance(status, str) or status not in KNOWN_STATUSES:
        raise AsxError("Asynx returned an unknown task status", code="invalid_task_status")
    local_id = str(row["local_id"])
    previous_status = str(row.get("status"))
    local_status = (
        "canceling"
        if previous_status in {"cancel_requested", "canceling"}
        and status not in TERMINAL_STATUSES
        else status
    )
    connection = connect_ledger_db()
    try:
        update_task(
            connection,
            local_id,
            status=local_status,
            remote=task,
            error=task.get("error") if status in {"failed", "timeout", "canceled", "blocked"} else None,
            billing=task.get("billing"),
            last_polled=utc_now(),
        )
        if local_status != previous_status:
            append_event(
                connection,
                local_id,
                "status_changed",
                {"from": previous_status, "to": local_status, "request_id": request_id},
            )
        connection.commit()
    finally:
        connection.close()
    connection = connect_ledger_db()
    try:
        current_row = get_ledger_task(connection, local_task_id=local_id) or row
    finally:
        connection.close()
    if status == "succeeded" and download:
        target_dir = output_dir or str(row.get("output_dir") or DEFAULT_OUTPUT_DIR)
        delivered, code = _download_task_result(
            client, task, request_id, target_dir, local_id=local_id, modality=row.get("modality", "image")
        )
        for key in ("operation", "created_at", "updated_at", "last_polled", "modality"):
            if current_row.get(key) is not None:
                delivered[key] = current_row[key]
        return delivered, code
    payload = _local_task_payload(current_row)
    payload["request_id"] = request_id
    return payload, 0 if status not in {"failed", "timeout", "canceled", "blocked"} else 4


def refresh_task(client: AsynxClient, identifier: str) -> tuple[dict[str, Any], int]:
    connection = connect_ledger_db()
    try:
        row = get_ledger_task(connection, local_task_id=identifier) or get_ledger_task(
            connection, remote_task_id=identifier
        )
    finally:
        connection.close()
    if row is not None:
        if not row.get("remote_task_id"):
            return _local_task_payload(row), 0
        return sync_local_task(client, identifier)
    if identifier.startswith("local_"):
        raise AsxError(f"本地任务不存在：{identifier}", code="task_not_found")
    task, request_id = client.task(identifier)
    if task.get("status") == "succeeded":
        return _download_task_result(client, task, request_id, DEFAULT_OUTPUT_DIR)
    payload = task_payload(task, request_id)
    return payload, 0 if payload["ok"] else 4


def recover_tasks(
    client: AsynxClient,
    identifier: str | None = None,
    *,
    limit: int = 50,
    modality: str | None = None,
) -> dict[str, Any]:
    connection = connect_ledger_db()
    try:
        rows = (
            [_task_identifier_row(connection, identifier)]
            if identifier
            else list_ledger_tasks(connection, modality=modality, limit=limit)
        )
    finally:
        connection.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status"))
        if status in TERMINAL_STATUSES:
            if not identifier:
                continue
            if status != "succeeded":
                results.append(_local_task_payload(row))
                continue
        try:
            if not row.get("remote_task_id"):
                connection = connect_ledger_db()
                try:
                    blocked = cancel_blocked_batch_intent(connection, str(row["idempotency_key"]))
                    connection.commit()
                finally:
                    connection.close()
                if blocked:
                    if identifier:
                        results.append(_local_task_payload({**row, "status": "canceled"}))
                    continue
                body = row.get("request")
                if not isinstance(body, dict):
                    raise AsxError("本地请求体损坏，无法恢复", code="invalid_task_state")
                task_type = body.get("task_type") or f"{row.get('modality', 'image')}.{row.get('operation', 'generate')}"
                task, request_id = client.submit(task_type, body, str(row["idempotency_key"]))
                local_id = str(row["local_id"])
                bind_remote_task(connection, local_id, str(task.get("id")), remote=task, status=task.get("status", "queued"))
            synced, _code = sync_local_task(client, str(row["local_id"]))
            results.append(synced)
        except AsxError as exc:
            results.append({**_local_task_payload(row), "ok": False, "error": exc.payload()["error"]})
    return {"ok": all(item.get("ok", False) for item in results), "schema_version": SCHEMA_VERSION, "tasks": results}


def local_tasks_payload(
    *, modality: str | None = None, status: str | None = None, limit: int = 50
) -> dict[str, Any]:
    connection = connect_ledger_db()
    try:
        rows = list_ledger_tasks(connection, modality=modality, status=status, limit=limit)
        return {"ok": True, "schema_version": SCHEMA_VERSION, "tasks": [_local_task_payload(row) for row in rows]}
    finally:
        connection.close()


def local_task_status(identifier: str) -> dict[str, Any]:
    connection = connect_ledger_db()
    try:
        return {"ok": True, "schema_version": SCHEMA_VERSION, "task": _local_task_payload(_task_identifier_row(connection, identifier))}
    finally:
        connection.close()


def local_asset_payload(identifier: str) -> dict[str, Any]:
    connection = connect_ledger_db()
    try:
        row = _task_identifier_row(connection, identifier)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "local_id": row["local_id"],
            "task_id": row.get("remote_task_id"),
            "assets": [
                {
                    **asset,
                    "missing": bool(asset.get("path"))
                    and not Path(str(asset["path"])).is_file(),
                }
                for asset in list_assets(connection, str(row["local_id"]))
            ],
        }
    finally:
        connection.close()


def poll_local_tasks(
    client: AsynxClient,
    identifier: str | None = None,
    *,
    limit: int = 50,
    modality: str | None = None,
) -> dict[str, Any]:
    connection = connect_ledger_db()
    try:
        if identifier:
            rows = [_task_identifier_row(connection, identifier)]
        else:
            rows = list_ledger_tasks(connection, modality=modality, limit=limit)
    finally:
        connection.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status"))
        if status in TERMINAL_STATUSES and status != "succeeded":
            results.append(_local_task_payload(row))
            continue
        if not row.get("remote_task_id"):
            results.append(_local_task_payload(row))
            continue
        try:
            synced, _code = sync_local_task(client, str(row["local_id"]))
            results.append(synced)
        except AsxError as exc:
            results.append(
                {
                    "ok": False,
                    "local_id": row.get("local_id"),
                    "task_id": row.get("remote_task_id"),
                    "error": exc.payload().get("error"),
                }
            )
    return {"ok": all(item.get("ok", False) for item in results), "schema_version": SCHEMA_VERSION, "tasks": results}


def cancel_local_task(client: AsynxClient, identifier: str) -> tuple[dict[str, Any], int]:
    connection = connect_ledger_db()
    try:
        row = _task_identifier_row(connection, identifier)
    finally:
        connection.close()
    local_id = str(row["local_id"])
    remote_id = row.get("remote_task_id")
    if not isinstance(remote_id, str):
        connection = connect_ledger_db()
        try:
            update_task(connection, local_id, status="canceled")
            append_event(connection, local_id, "canceled", {"remote": False})
            connection.commit()
        finally:
            connection.close()
        return local_task_status(local_id), 0
    task, request_id = client.cancel(remote_id)
    remote_status = task.get("status")
    status = remote_status if isinstance(remote_status, str) else "canceling"
    local_status = status if status in TERMINAL_STATUSES else "cancel_requested"
    connection = connect_ledger_db()
    try:
        update_task(
            connection,
            local_id,
            status=local_status,
            remote=task,
            billing=task.get("billing"),
            error=task.get("error") if local_status in TERMINAL_STATUSES else None,
            last_polled=utc_now(),
        )
        append_event(
            connection,
            local_id,
            "cancel_requested",
            {"request_id": request_id, "remote_status": status},
        )
        connection.commit()
    finally:
        connection.close()
    payload = local_task_status(local_id)
    if local_status not in TERMINAL_STATUSES:
        payload["cancel_requested"] = True
        payload["warning"] = "上游取消可能仍在处理，已开始的任务可能继续执行并计费"
    return payload, 0


def task_payload(
    task: dict[str, Any],
    request_id: str | None,
    *,
    files: list[str] | None = None,
    local_id: str | None = None,
    modality: str = "image",
) -> dict[str, Any]:
    status = task.get("status")
    if not isinstance(status, str) or status not in KNOWN_STATUSES:
        raise AsxError("Asynx 返回了未知的任务状态", code="invalid_task_status", task_id=task.get("id"))
    payload: dict[str, Any] = {
        "ok": status == "succeeded" or status not in TERMINAL_STATUSES,
        "task_id": task.get("id"),
        "status": status,
        "model": task.get("model"),
        "result_quality": task.get("result_quality"),
        "billing": task.get("billing"),
        "request_id": request_id,
        "schema_version": SCHEMA_VERSION,
        "local_id": local_id,
        "modality": modality,
    }
    if files is not None:
        payload["files"] = files
    if status in TERMINAL_STATUSES and status != "succeeded":
        payload["ok"] = False
        payload["error"] = task.get("error") or {
            "code": f"task_{status}",
            "message": f"Task ended with status {status}",
        }
    if files is None:
        _task_followup(payload)
    return {key: value for key, value in payload.items() if value is not None}


def _download_task_result(
    client: AsynxClient,
    task: dict[str, Any],
    request_id: str | None,
    output_dir: str,
    *,
    local_id: str | None = None,
    metrics: list[dict[str, Any]] | None = None,
    modality: str = "image",
    parent_paths: Sequence[str | Path] | None = None,
    operation: str | None = None,
    category: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    files: list[str] = []
    download_error: dict[str, Any] | None = None
    try:
        download_assets(
            client,
            task,
            output_dir,
            local_task_id=local_id,
            metrics=metrics,
            completed_files=files,
            modality=modality,
            parent_paths=parent_paths,
            operation=operation,
            category=category,
            metadata=metadata,
        )
    except AsxError as exc:
        download_error = exc.payload()["error"]
    except (OSError, sqlite3.Error) as exc:
        download_error = {"code": "output_write_failed", "message": f"结果保存失败：{exc}"}
    payload = task_payload(
        task, request_id, files=files, local_id=local_id, modality=modality
    )
    payload["download_status"] = "failed" if download_error else "downloaded"
    if download_error:
        payload["ok"] = False
        payload["error"] = {**download_error, "phase": "download"}
        payload.setdefault("warnings", []).append({
            "code": "download_incomplete",
            "message": "资源已生成，但下载未完成；已保存的文件仍可使用。请继续下载原任务，不要重新生成。",
        })
        _task_followup(payload)
        return payload, 4
    return payload, 0


def _persist_task_intent(
    client: AsynxClient,
    body: dict[str, Any],
    *,
    operation: str,
    model: str,
    idempotency_key: str,
    output_dir: str,
    modality: str = "image",
    parent_paths: Sequence[str | Path] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    local_id = f"local_{secrets.token_hex(12)}"
    resolved_output_dir = (
        str((Path(DEFAULT_OUTPUT_DIR) / local_id).resolve())
        if output_dir == DEFAULT_OUTPUT_DIR
        else str(Path(output_dir).expanduser().resolve())
    )
    connection = connect_ledger_db()
    try:
        row = create_task_intent(
            connection,
            operation=operation,
            model=model,
            idempotency_key=idempotency_key,
            request=body,
            output_dir=resolved_output_dir,
            base_url=client.base_url,
            local_id=local_id,
            status="submitting",
            modality=modality,
        )
        append_event(connection, str(row["local_id"]), "submit_started")
        if parent_paths or metadata:
            append_event(
                connection,
                str(row["local_id"]),
                "lineage_parents",
                {
                    "parent_paths": [str(p) for p in (parent_paths or [])],
                    "metadata": dict(metadata) if metadata else None,
                },
            )
        connection.commit()
        return row
    finally:
        connection.close()


def _bind_submission(
    client: AsynxClient,
    local_id: str,
    task: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    remote_id = task.get("id")
    if not isinstance(remote_id, str):
        raise AsxError("Task response is missing an ID", code="invalid_task_response")
    status = task.get("status") if isinstance(task.get("status"), str) else "queued"
    connection = connect_ledger_db()
    try:
        row = bind_remote_task(
            connection,
            local_id,
            remote_id,
            remote=task,
            status=status,
            last_polled=utc_now(),
        )
        update_task(
            connection,
            local_id,
            billing=task.get("billing") if task.get("billing") is not None else None,
        )
        append_event(
            connection,
            local_id,
            "submitted",
            {"request_id": request_id, "status": status},
        )
        connection.commit()
        return row
    finally:
        connection.close()


def _record_submit_error(local_id: str, exc: AsxError) -> None:
    connection = connect_ledger_db()
    try:
        update_task(connection, local_id, error=exc.payload().get("error"))
        append_event(connection, local_id, "submit_error", exc.payload().get("error"))
        connection.commit()
    finally:
        connection.close()


def _update_local_snapshot(
    local_id: str,
    task: dict[str, Any],
    request_id: str | None,
) -> None:
    status = task.get("status")
    if not isinstance(status, str):
        raise AsxError("Asynx returned an unknown task status", code="invalid_task_status")
    connection = connect_ledger_db()
    try:
        current = get_ledger_task(connection, local_task_id=local_id)
        if current is None:
            raise AsxError(f"本地 Task 不存在：{local_id}", code="task_not_found")
        previous = str(current.get("status"))
        update_task(
            connection,
            local_id,
            status=status,
            remote=task,
            error=task.get("error") if status in {"failed", "timeout", "canceled", "blocked"} else None,
            billing=task.get("billing"),
            last_polled=utc_now(),
        )
        if status != previous:
            append_event(
                connection,
                local_id,
                "status_changed",
                {"from": previous, "to": status, "request_id": request_id},
            )
        connection.commit()
    finally:
        connection.close()


def run_task_submission(
    client: AsynxClient,
    *,
    task_type: str,
    body: dict[str, Any],
    model: str,
    output_dir: str,
    idempotency_key: str,
    detach: bool = False,
    modality: str = "image",
    input_warnings: list[dict[str, Any]] | None = None,
    catalog_request_id: str | None = None,
    started_at: float | None = None,
    parent_paths: Sequence[str | Path] | None = None,
    category: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    start_time = started_at or time.perf_counter()
    initial_delay = initial_poll_delay_seconds() if not detach else 0.0
    operation = task_type.split(".", 1)[-1] if "." in task_type else task_type

    _ensure_partner_quota(client)

    local_row = _persist_task_intent(
        client,
        body,
        operation=operation,
        model=model,
        idempotency_key=idempotency_key,
        output_dir=output_dir,
        modality=modality,
        parent_paths=parent_paths,
        metadata=metadata,
    )
    local_id = str(local_row["local_id"])
    resolved_output_dir = str(local_row.get("output_dir") or output_dir)
    log(f"提交 {task_type}，模型：{model}")
    log(f"幂等键：{idempotency_key}")
    submitted_at = time.perf_counter()
    try:
        if local_row.get("remote_task_id"):
            task, request_id = client.task(str(local_row["remote_task_id"]))
        else:
            task, request_id = client.submit(task_type, body, idempotency_key)
            _bind_submission(client, local_id, task, request_id)
    except AsxError as exc:
        _record_submit_error(local_id, exc)
        if exc.details is None:
            exc.details = {}
        if isinstance(exc.details, dict):
            exc.details.setdefault("local_id", local_id)
            exc.details.setdefault("next_action_args", ["task", "recover", local_id])
            exc.details.setdefault("idempotency_key", idempotency_key)
            if catalog_request_id:
                exc.details.setdefault("catalog_request_id", catalog_request_id)
            if input_warnings:
                exc.details.setdefault("warnings", input_warnings)
        raise
    accepted_at = time.perf_counter()
    timings: dict[str, Any] = {
        "submit_seconds": round(accepted_at - submitted_at, 3),
    }
    if detach:
        payload = task_payload(task, request_id, local_id=local_id, modality=modality)
        payload["idempotency_key"] = idempotency_key
        payload["timings"] = timings
        if input_warnings:
            payload["warnings"] = input_warnings
        return payload, 0 if payload["ok"] else 4

    task, request_id = wait_for_terminal(
        client,
        task,
        request_id,
        initial_delay=MAX_TASK_POLL_DELAY_SECONDS if local_row.get("remote_task_id") else initial_delay,
    )
    _update_local_snapshot(local_id, task, request_id)
    finished_at = time.perf_counter()
    timings["wait_seconds"] = round(finished_at - accepted_at, 3)
    if task.get("status") != "succeeded":
        payload = task_payload(task, request_id, local_id=local_id, modality=modality)
        payload["timings"] = timings
        if input_warnings:
            payload["warnings"] = input_warnings
        return payload, 4

    asset_metrics: list[dict[str, Any]] = []
    payload, code = _download_task_result(
        client,
        task,
        request_id,
        resolved_output_dir,
        metrics=asset_metrics,
        local_id=local_id,
        modality=modality,
        parent_paths=parent_paths,
        operation=operation,
        category=category,
        metadata=metadata,
    )
    timings["download_seconds"] = round(time.perf_counter() - finished_at, 3)
    timings["asset_downloads"] = asset_metrics
    payload["timings"] = timings
    if input_warnings:
        payload.setdefault("warnings", []).extend(input_warnings)
    return payload, code


def quote_task_payload(
    client: AsynxClient,
    *,
    task_type: str,
    body: dict[str, Any],
    model: str,
) -> tuple[dict[str, Any], int]:
    quote_data, request_id = client.quote(task_type, body)
    payload: dict[str, Any] = {
        "ok": True,
        "quoted": True,
        "task_type": task_type,
        "model": model,
        "quote": quote_data,
    }
    if request_id:
        payload["request_id"] = request_id
    return payload, 0


def run_submission(
    client: AsynxClient, args: argparse.Namespace, task_type: str
) -> tuple[dict[str, Any], int]:
    from .images import build_task, task_input_warnings, validate_idempotency_key

    started_at = time.perf_counter()
    log(f"正在准备 {task_type} 请求")
    references = args.reference if task_type == "image.generate" else args.image
    from_task = getattr(args, "from_task", None)
    if task_type == "image.edit" and from_task:
        if references:
            raise AsxError(
                "Use either --image or --from-task, not both", code="conflicting_edit_inputs"
            )
        connection = connect_ledger_db()
        try:
            references = ledger_task_paths(connection, from_task)
        finally:
            connection.close()
    mask_arg = args.mask if task_type == "image.edit" else None
    parent_paths: list[str] = [p for p in (references or []) if isinstance(p, str) and p.strip()]
    if isinstance(mask_arg, str) and mask_arg.strip():
        parent_paths.append(mask_arg)
    body, model, catalog_request_id = build_task(
        client,
        task_type=task_type,
        model_selector=args.model,
        prompt=args.prompt,
        image_size=args.image_size,
        aspect_ratio=args.aspect_ratio,
        quality=args.quality,
        count=args.count,
        output_format=args.output_format,
        references=references,
        mask=mask_arg,
        keep_reference_original=args.keep_reference_original,
    )
    if getattr(args, "quote", False):
        return quote_task_payload(client, task_type=task_type, body=body, model=model)
    input_warnings = task_input_warnings(body)
    for warning in input_warnings:
        log(f"提示：{warning['message']}")
    idempotency_key = validate_idempotency_key(
        args.idempotency_key or f"asx-{secrets.token_hex(16)}"
    )
    return run_task_submission(
        client,
        task_type=task_type,
        body=body,
        model=model,
        output_dir=args.output_dir,
        idempotency_key=idempotency_key,
        detach=args.detach,
        modality="image",
        input_warnings=input_warnings,
        catalog_request_id=catalog_request_id,
        started_at=started_at,
        parent_paths=parent_paths,
    )


def models_payload(
    client: AsynxClient,
    operation: str,
    *,
    modality: str | None = None,
    guide: str | None = None,
) -> dict[str, Any]:
    if guide:
        guide_data, request_id = client.model_guide(guide)
        payload: dict[str, Any] = {"ok": True, "guide": guide_data}
        if request_id:
            payload["request_id"] = request_id
        return payload
    models, request_id = client.models(modality=modality)
    target_modality = modality or "image"
    task_type = None if operation == "all" else f"{target_modality}.{operation}"
    items = []
    for model in models:
        if not isinstance(model, dict):
            continue
        if task_type and task_type not in model.get("task_types", []):
            continue
        items.append(
            {
                key: model.get(key)
                for key in (
                    "name",
                    "task_types",
                    "capabilities",
                    "pricing",
                    "official_discount_percent",
                )
                if model.get(key) is not None
            }
        )
    payload: dict[str, Any] = {"ok": True, "models": items}
    if request_id:
        payload["request_id"] = request_id
    return payload


def history_payload(
    client: AsynxClient,
    *,
    status: str | None = None,
    model: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    data, request_id = client.list_tasks(status=status, limit=limit)
    items = data.get("items")
    if not isinstance(items, list):
        items = data.get("tasks", [])
    payload: dict[str, Any] = {"ok": True, "tasks": items}
    next_cursor = data.get("next_cursor")
    if next_cursor:
        payload["next_cursor"] = next_cursor
    if request_id:
        payload["request_id"] = request_id
    return payload


def wait_and_download(
    client: AsynxClient, task_id: str, output_dir: str = DEFAULT_OUTPUT_DIR
) -> tuple[dict[str, Any], int]:
    ledger_connection = connect_ledger_db()
    try:
        local_row = get_ledger_task(ledger_connection, local_task_id=task_id)
        if local_row is None:
            local_row = get_ledger_task(ledger_connection, remote_task_id=task_id)
    finally:
        ledger_connection.close()

    modality = local_row.get("modality", "image") if local_row else "image"
    if local_row is not None:
        task, request_id = _remote_task_for_row(client, local_row)
        task, request_id = wait_for_terminal(client, task, request_id)
        local_id = str(local_row["local_id"])
        connection = connect_ledger_db()
        try:
            update_task(
                connection,
                local_id,
                status=task.get("status"),
                remote=task,
                last_polled=utc_now(),
            )
            connection.commit()
        finally:
            connection.close()

        if task.get("status") != "succeeded":
            return task_payload(task, request_id, local_id=local_id, modality=modality), 4
        target_dir = (
            str(local_row.get("output_dir") or DEFAULT_OUTPUT_DIR)
            if output_dir == DEFAULT_OUTPUT_DIR
            else output_dir
        )
        download_started = time.perf_counter()
        asset_metrics: list[dict[str, Any]] = []
        payload, code = _download_task_result(
            client,
            task,
            request_id,
            target_dir,
            metrics=asset_metrics,
            local_id=local_id,
            modality=modality,
        )
        payload["timings"] = {
            "download_seconds": round(time.perf_counter() - download_started, 3),
            "asset_downloads": asset_metrics,
        }
        return payload, code

    task, request_id = client.task(task_id)
    task, request_id = wait_for_terminal(client, task, request_id)
    if task.get("status") != "succeeded":
        return task_payload(task, request_id, modality=modality), 4
    download_started = time.perf_counter()
    remote_asset_metrics: list[dict[str, Any]] = []
    payload, code = _download_task_result(
        client,
        task,
        request_id,
        output_dir,
        metrics=remote_asset_metrics,
        modality=modality,
    )
    payload["timings"] = {
        "download_seconds": round(time.perf_counter() - download_started, 3),
        "asset_downloads": remote_asset_metrics,
    }
    return payload, code
