"""Versioned local task ledger used by the Asynx skills.

Schema version 2 adds multi-modal scoping (image, audio, video, 3d) to tasks and batches,
with seamless zero-data-loss migration from version 1.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import state_path
from .constants import UTC
from .errors import AsxError

SCHEMA_VERSION = 2
DB_TIMEOUT_SECONDS = 30
DB_BUSY_TIMEOUT_MILLISECONDS = 30_000

_TASK_FIELDS = frozenset(
    {
        "operation",
        "model",
        "status",
        "idempotency_key",
        "request_json",
        "output_dir",
        "remote_task_id",
        "remote_json",
        "error_json",
        "billing",
        "last_polled",
        "base_url",
        "created_at",
        "updated_at",
        "modality",
    }
)
_JSON_FIELDS = {"request_json", "remote_json", "error_json", "billing"}
_JSON_ALIASES = {
    "request_json": "request",
    "remote_json": "remote",
    "error_json": "error",
}
_REQUIRED_COLUMNS = {
    "tasks": {
        "local_id",
        "remote_task_id",
        "operation",
        "model",
        "status",
        "idempotency_key",
        "request_json",
        "output_dir",
        "remote_json",
        "error_json",
        "billing",
        "created_at",
        "updated_at",
        "last_polled",
        "base_url",
        "modality",
    },
    "assets": {
        "artifact_id",
        "local_task_id",
        "remote_index",
        "path",
        "remote_url",
        "mime",
        "bytes",
        "sha256",
        "state",
        "created_at",
        "updated_at",
    },
    "task_events": {"id", "local_task_id", "event_type", "payload_json", "created_at"},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AsxError("State JSON value is invalid", code="invalid_state_json") from exc
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise AsxError(
            f"State value cannot be serialized as JSON: {exc}", code="invalid_state_json"
        ) from exc


def _decode(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _timestamp(value: str | None) -> str:
    return value or utc_now()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(zip(row.keys(), tuple(row)))
    for key in _JSON_FIELDS:
        if key in result:
            result[_JSON_ALIASES.get(key, key)] = _decode(result[key])
    return result


def _asset_id(local_task_id: str, remote_index: int) -> str:
    digest = hashlib.sha256(f"{local_task_id}\0{remote_index}".encode()).hexdigest()[:24]
    return f"artifact_{digest}"


def _path_for_database(path: Path | str | None) -> Path | None:
    if path is None:
        return state_path().expanduser()
    if str(path) == ":memory:":
        return None
    return Path(path).expanduser()


def connect_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the multi-modal ledger and apply connection-level safety settings."""
    database_path = _path_for_database(path)
    if database_path is None:
        connection = sqlite3.connect(":memory:", timeout=DB_TIMEOUT_SECONDS)
    else:
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(str(database_path), timeout=DB_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MILLISECONDS}")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        pass
    init_schema(connection)
    if database_path is not None:
        try:
            if database_path.parent.name == "asx":
                database_path.parent.chmod(stat.S_IRWXU)
            if database_path.exists():
                database_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    """Create or migrate the schema to SCHEMA_VERSION = 2 with zero data loss."""
    if connection.row_factory is None:
        connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise AsxError(
            f"State database schema {version} is newer than supported version {SCHEMA_VERSION}",
            code="unsupported_state_schema",
        )

    # Version 1 -> Version 2 migration
    if version == 1:
        # Check if tasks table exists and needs modality column
        task_cols = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "modality" not in task_cols:
            connection.execute("ALTER TABLE tasks ADD COLUMN modality TEXT NOT NULL DEFAULT 'image'")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_tasks_modality_updated ON tasks(modality, updated_at DESC)"
        )

        # Also migrate batches if table exists
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "batches" in existing_tables:
            batch_cols = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(batches)").fetchall()
            }
            if "modality" not in batch_cols:
                connection.execute("ALTER TABLE batches ADD COLUMN modality TEXT NOT NULL DEFAULT 'image'")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_batches_modality_updated ON batches(modality, updated_at DESC)"
            )

        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        return

    # Fresh creation (version == 0)
    if version == 0:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                local_id TEXT PRIMARY KEY,
                remote_task_id TEXT UNIQUE,
                operation TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                remote_json TEXT,
                error_json TEXT,
                billing TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_polled TEXT,
                base_url TEXT NOT NULL,
                modality TEXT NOT NULL DEFAULT 'image'
            );
            CREATE INDEX IF NOT EXISTS ix_tasks_status_updated
                ON tasks(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS ix_tasks_operation_created
                ON tasks(operation, created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_tasks_modality_updated
                ON tasks(modality, updated_at DESC);
            CREATE TABLE IF NOT EXISTS assets (
                artifact_id TEXT PRIMARY KEY,
                local_task_id TEXT NOT NULL REFERENCES tasks(local_id) ON DELETE CASCADE,
                remote_index INTEGER NOT NULL CHECK (remote_index >= 0),
                path TEXT NOT NULL,
                remote_url TEXT,
                mime TEXT,
                bytes INTEGER,
                sha256 TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(local_task_id, remote_index)
            );
            CREATE INDEX IF NOT EXISTS ix_assets_task_index
                ON assets(local_task_id, remote_index);
            CREATE INDEX IF NOT EXISTS ix_assets_state_updated
                ON assets(state, updated_at DESC);
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_task_id TEXT NOT NULL REFERENCES tasks(local_id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_task_events_task_created
                ON task_events(local_task_id, created_at, id);
            PRAGMA user_version = 2;
            """
        )
        connection.commit()


def ensure_batch_schema(connection: sqlite3.Connection, *, default_modality: str = "image") -> None:
    """Add the multi-modal batch tables to an already-open ledger DB."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'completed', 'canceled')),
            model TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            template_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            modality TEXT NOT NULL DEFAULT 'image'
        );
        CREATE TABLE IF NOT EXISTS batch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            body_json TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            task_id TEXT,
            status TEXT NOT NULL,
            request_id TEXT,
            files_json TEXT,
            error_json TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(batch_id, sequence)
        );
        CREATE INDEX IF NOT EXISTS ix_batch_items_batch_status
            ON batch_items(batch_id, status, sequence);
        CREATE INDEX IF NOT EXISTS ix_batches_modality_updated
            ON batches(modality, updated_at DESC);
        """
    )
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(batches)").fetchall()
    }
    if "cancel_requested" not in columns:
        connection.execute(
            "ALTER TABLE batches ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
        )
    if "modality" not in columns:
        connection.execute(
            "ALTER TABLE batches ADD COLUMN modality TEXT NOT NULL DEFAULT 'image'"
        )
    connection.commit()


def create_task_intent(
    connection: sqlite3.Connection,
    *,
    operation: str,
    model: str,
    idempotency_key: str,
    request: Mapping[str, Any] | str | None = None,
    request_json: Mapping[str, Any] | str | None = None,
    output_dir: str = "",
    base_url: str = "",
    local_id: str | None = None,
    status: str = "intent",
    created_at: str | None = None,
    modality: str = "image",
) -> dict[str, Any]:
    if not operation.strip() or not model.strip() or not idempotency_key.strip():
        raise AsxError(
            "Task intent requires operation, model, and idempotency key",
            code="invalid_task_intent",
        )
    if (request is None) == (request_json is None):
        raise ValueError("provide exactly one request or request_json")
    serialized_request = _json(request if request is not None else request_json)
    now = _timestamp(created_at)
    candidate_id = local_id or f"local_{secrets.token_hex(12)}"
    existing = connection.execute(
        "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing is not None:
        if (
            existing["request_json"] != serialized_request
            or existing["operation"] != operation
            or existing["model"] != model
        ):
            raise AsxError(
                "Idempotency key is already associated with a different task intent",
                code="idempotency_conflict",
                details={"local_id": existing["local_id"]},
            )
        return _row_dict(existing)
    try:
        connection.execute(
            "INSERT INTO tasks "
            "(local_id, remote_task_id, operation, model, status, idempotency_key, request_json, "
            "output_dir, remote_json, error_json, billing, created_at, updated_at, "
            "last_polled, base_url, modality) "
            "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, ?, ?)",
            (
                candidate_id,
                operation,
                model,
                status,
                idempotency_key,
                serialized_request,
                output_dir,
                now,
                now,
                base_url,
                modality,
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raced = connection.execute(
            "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if raced is not None and raced["request_json"] == serialized_request:
            return _row_dict(raced)
        raise AsxError(f"Cannot create task intent: {exc}", code="task_state_conflict") from exc
    row = connection.execute("SELECT * FROM tasks WHERE local_id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise AsxError("Task intent was not persisted", code="task_state_write_failed")
    return _row_dict(row)


def get_task(
    connection: sqlite3.Connection,
    local_task_id: str | None = None,
    *,
    remote_task_id: str | None = None,
) -> dict[str, Any] | None:
    """Find a task across all modalities by either local_id or remote_task_id."""
    if (local_task_id is None) == (remote_task_id is None):
        raise ValueError("provide exactly one local_task_id or remote_task_id")
    if local_task_id is not None:
        row = connection.execute(
            "SELECT * FROM tasks WHERE local_id = ?", (local_task_id,)
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM tasks WHERE remote_task_id = ?", (remote_task_id,)
        ).fetchone()
    return _row_dict(row) if row is not None else None


def cancel_blocked_batch_intent(connection: sqlite3.Connection, idempotency_key: str) -> bool:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'batch_items'"
    ).fetchone() is None:
        return False
    blocked = connection.execute(
        "SELECT 1 FROM batch_items i JOIN batches b ON b.id = i.batch_id "
        "WHERE i.idempotency_key = ? AND "
        "(b.cancel_requested = 1 OR b.status = 'canceled' OR i.status = 'canceled')",
        (idempotency_key,),
    ).fetchone()
    if blocked is None:
        return False
    connection.execute(
        "UPDATE tasks SET status = 'canceled', updated_at = ? "
        "WHERE idempotency_key = ? AND remote_task_id IS NULL",
        (utc_now(), idempotency_key),
    )
    connection.commit()
    return True


def list_tasks(
    connection: sqlite3.Connection,
    *,
    modality: str | None = None,
    status: str | None = None,
    operation: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List tasks, filtering by modality unless modality is None."""
    clauses: list[str] = []
    values: list[Any] = []
    if modality is not None:
        clauses.append("modality = ?")
        values.append(modality)
    if status:
        clauses.append("status = ?")
        values.append(status)
    if operation:
        clauses.append("operation = ?")
        values.append(operation)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    bounded = max(1, min(int(limit), 1000))
    rows = connection.execute(
        f"SELECT * FROM tasks{where} "
        "ORDER BY updated_at DESC, created_at DESC, local_id DESC LIMIT ?",
        (*values, bounded),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def bind_remote_task(
    connection: sqlite3.Connection,
    local_task_id: str,
    remote_task_id: str,
    *,
    remote: Mapping[str, Any] | str | None = None,
    remote_json: Mapping[str, Any] | str | None = None,
    status: str | None = None,
    last_polled: str | None = None,
) -> dict[str, Any]:
    if not remote_task_id.strip():
        raise AsxError("Remote Task ID is empty", code="invalid_task_response")
    fields: dict[str, Any] = {"remote_task_id": remote_task_id}
    if remote is not None and remote_json is not None:
        raise ValueError("provide at most one remote or remote_json")
    if remote_json is not None:
        fields["remote_json"] = remote_json
    elif remote is not None:
        fields["remote_json"] = remote
    if status is not None:
        fields["status"] = status
    if last_polled is not None:
        fields["last_polled"] = last_polled
    try:
        result = update_task(connection, local_task_id, **fields)
    except sqlite3.IntegrityError as exc:
        raise AsxError(
            f"Remote Task ID is already bound: {remote_task_id}",
            code="task_state_conflict",
        ) from exc
    append_event(connection, local_task_id, "remote_bound", {"remote_task_id": remote_task_id})
    return result


def update_task(
    connection: sqlite3.Connection, local_task_id: str, **fields: Any
) -> dict[str, Any]:
    unknown = set(fields) - _TASK_FIELDS - {
        "remote",
        "error",
        "billing_json",
        "request",
    }
    if unknown:
        raise ValueError(f"unsupported task fields: {', '.join(sorted(unknown))}")
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        target = {
            "remote": "remote_json",
            "error": "error_json",
            "billing_json": "billing",
            "request": "request_json",
        }.get(key, key)
        updates[target] = _json(value) if target in _JSON_FIELDS and value is not None else value
    updates.setdefault("updated_at", utc_now())
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = [updates[key] for key in updates]
    values.append(local_task_id)
    cursor = connection.execute(f"UPDATE tasks SET {assignments} WHERE local_id = ?", (*values,))
    connection.commit()
    if cursor.rowcount != 1:
        raise AsxError(f"Task not found: {local_task_id}", code="task_not_found")
    row = connection.execute("SELECT * FROM tasks WHERE local_id = ?", (local_task_id,)).fetchone()
    if row is None:
        raise AsxError(f"Task not found: {local_task_id}", code="task_not_found")
    return _row_dict(row)


def append_event(
    connection: sqlite3.Connection,
    local_task_id: str,
    event_type: str,
    payload: Mapping[str, Any] | str | None = None,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not event_type.strip():
        raise AsxError("Task event type is empty", code="invalid_task_event")
    if get_task(connection, local_task_id) is None:
        raise AsxError(f"Task not found: {local_task_id}", code="task_not_found")
    timestamp = _timestamp(created_at)
    payload_json = _json(payload) if payload is not None else None
    cursor = connection.execute(
        "INSERT INTO task_events "
        "(local_task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (local_task_id, event_type, payload_json, timestamp),
    )
    connection.commit()
    return {
        "id": int(cursor.lastrowid or 0),
        "local_task_id": local_task_id,
        "event_type": event_type,
        "payload": _decode(payload_json),
        "created_at": timestamp,
    }


def upsert_asset(
    connection: sqlite3.Connection,
    local_task_id: str,
    remote_index: int,
    *,
    path: str = "",
    remote_url: str | None = None,
    mime: str | None = None,
    bytes: int | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    state: str = "available",
    artifact_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if remote_index < 0:
        raise AsxError("Asset index must be non-negative", code="invalid_asset")
    if get_task(connection, local_task_id) is None:
        raise AsxError(f"Task not found: {local_task_id}", code="task_not_found")
    now = _timestamp(created_at)
    stable_id = artifact_id or _asset_id(local_task_id, remote_index)
    byte_count = size_bytes if size_bytes is not None else bytes
    connection.execute(
        "INSERT INTO assets "
        "(artifact_id, local_task_id, remote_index, path, remote_url, mime, bytes, sha256, "
        "state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(local_task_id, remote_index) DO UPDATE SET "
        "path = excluded.path, remote_url = excluded.remote_url, mime = excluded.mime, "
        "bytes = excluded.bytes, "
        "sha256 = excluded.sha256, state = excluded.state, updated_at = excluded.updated_at",
        (
            stable_id,
            local_task_id,
            remote_index,
            path,
            remote_url,
            mime,
            byte_count,
            sha256,
            state,
            now,
            now,
        ),
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM assets WHERE local_task_id = ? AND remote_index = ?",
        (local_task_id, remote_index),
    ).fetchone()
    if row is None:
        raise AsxError("Asset was not persisted", code="asset_state_write_failed")
    return dict(zip(row.keys(), tuple(row)))


def list_assets(
    connection: sqlite3.Connection,
    local_task_id: str,
    *,
    state: str | None = None,
) -> list[dict[str, Any]]:
    if state is None:
        rows = connection.execute(
            "SELECT * FROM assets WHERE local_task_id = ? ORDER BY remote_index", (local_task_id,)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM assets WHERE local_task_id = ? AND state = ? ORDER BY remote_index",
            (local_task_id, state),
        ).fetchall()
    return [dict(zip(row.keys(), tuple(row))) for row in rows]


def task_paths(
    connection: sqlite3.Connection,
    local_task_id: str,
    *,
    include_missing: bool = False,
) -> list[str]:
    task = get_task(connection, local_task_id)
    if task is None:
        task = get_task(connection, remote_task_id=local_task_id)
    if task is None:
        raise AsxError(f"Task not found: {local_task_id}", code="task_not_found")
    local_id = str(task["local_id"])
    assets = list_assets(connection, local_id)
    if not assets:
        raise AsxError(
            f"No locally indexed assets for Task {local_task_id}",
            code="asset_not_found",
            task_id=local_task_id,
        )
    paths: list[str] = []
    for asset in assets:
        path = asset["path"]
        if not isinstance(path, str) or not path:
            continue
        if not include_missing and asset["state"] in {"deleted", "missing"}:
            continue
        if not include_missing and not Path(path).is_file():
            raise AsxError(
                f"Indexed asset file is missing: {path}",
                code="asset_file_missing",
                details={"local_task_id": local_task_id, "path": path},
            )
        if include_missing or asset["state"] not in {"deleted", "missing"}:
            paths.append(path)
    return paths


__all__ = [
    "SCHEMA_VERSION",
    "append_event",
    "bind_remote_task",
    "connect_db",
    "create_task_intent",
    "ensure_batch_schema",
    "get_task",
    "init_schema",
    "list_assets",
    "list_tasks",
    "task_paths",
    "update_task",
    "upsert_asset",
    "utc_now",
]
