"""Versioned local task ledger used by the Asynx skills.

Schema version 3 adds asset classification/source metadata and directed parent-child
asset lineage (asset_lineage) across cloud generation tasks, local post-processing,
and Local UI Workbench exports, with seamless zero-data-loss migration from v1 and v2.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import sqlite3
import stat
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import state_path
from .constants import UTC
from .errors import AsxError

SCHEMA_VERSION = 3
DB_TIMEOUT_SECONDS = 30
DB_BUSY_TIMEOUT_MILLISECONDS = 30_000
_LOCAL_TASK_BASE_URLS = ("local://process", "local://ui")

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
        "category",
        "source_kind",
        "operation",
        "metadata_json",
    },
    "asset_lineage": {
        "id",
        "parent_artifact_id",
        "parent_path",
        "parent_sha256",
        "child_artifact_id",
        "child_path",
        "child_sha256",
        "operation",
        "modality",
        "task_id",
        "created_at",
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


def compute_file_sha256(path: Path | str) -> str | None:
    """Return the hex SHA-256 digest of a regular local file, or None if unavailable."""
    target = Path(path).expanduser()
    try:
        if not target.is_file():
            return None
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def new_local_task_id(prefix: str = "local_proc") -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _canonical_path(value: str | Path) -> str:
    text = str(value).strip()
    if text.casefold().startswith(("http://", "https://", "data:")):
        return text
    return str(Path(text).expanduser().resolve())


def _asset_row_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(zip(row.keys(), tuple(row)))
    if "metadata_json" in result:
        result["metadata"] = _decode(result["metadata_json"])
    return result


def init_schema(connection: sqlite3.Connection) -> None:
    """Create or migrate the schema to SCHEMA_VERSION = 3 with zero data loss."""
    if connection.row_factory is None:
        connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise AsxError(
            f"State database schema {version} is newer than supported version {SCHEMA_VERSION}",
            code="unsupported_state_schema",
        )

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
                category TEXT,
                source_kind TEXT NOT NULL DEFAULT 'cloud_task',
                operation TEXT,
                metadata_json TEXT,
                UNIQUE(local_task_id, remote_index)
            );
            CREATE INDEX IF NOT EXISTS ix_assets_task_index
                ON assets(local_task_id, remote_index);
            CREATE INDEX IF NOT EXISTS ix_assets_state_updated
                ON assets(state, updated_at DESC);
            CREATE INDEX IF NOT EXISTS ix_assets_path
                ON assets(path);
            CREATE INDEX IF NOT EXISTS ix_assets_sha256
                ON assets(sha256);
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_task_id TEXT NOT NULL REFERENCES tasks(local_id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_task_events_task_created
                ON task_events(local_task_id, created_at, id);
            CREATE TABLE IF NOT EXISTS asset_lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_artifact_id TEXT,
                parent_path TEXT NOT NULL,
                parent_sha256 TEXT,
                child_artifact_id TEXT NOT NULL REFERENCES assets(artifact_id) ON DELETE CASCADE,
                child_path TEXT NOT NULL,
                child_sha256 TEXT,
                operation TEXT NOT NULL,
                modality TEXT NOT NULL,
                task_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(parent_path, child_artifact_id, operation)
            );
            CREATE INDEX IF NOT EXISTS ix_lineage_child
                ON asset_lineage(child_artifact_id);
            CREATE INDEX IF NOT EXISTS ix_lineage_parent_id
                ON asset_lineage(parent_artifact_id);
            CREATE INDEX IF NOT EXISTS ix_lineage_parent_path
                ON asset_lineage(parent_path);
            CREATE INDEX IF NOT EXISTS ix_lineage_parent_sha
                ON asset_lineage(parent_sha256);
            PRAGMA user_version = 3;
            """
        )
        connection.commit()
        return

    # Version 1 -> Version 2 migration (falls through to v2 -> v3)
    if version == 1:
        task_cols = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "modality" not in task_cols:
            connection.execute("ALTER TABLE tasks ADD COLUMN modality TEXT NOT NULL DEFAULT 'image'")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_tasks_modality_updated ON tasks(modality, updated_at DESC)"
        )

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
        version = 2

    # Version 2 -> Version 3 migration
    if version == 2:
        asset_cols = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(assets)").fetchall()
        }
        if "category" not in asset_cols:
            connection.execute("ALTER TABLE assets ADD COLUMN category TEXT")
        if "source_kind" not in asset_cols:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'cloud_task'"
            )
        if "operation" not in asset_cols:
            connection.execute("ALTER TABLE assets ADD COLUMN operation TEXT")
        if "metadata_json" not in asset_cols:
            connection.execute("ALTER TABLE assets ADD COLUMN metadata_json TEXT")

        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS ix_assets_path
                ON assets(path);
            CREATE INDEX IF NOT EXISTS ix_assets_sha256
                ON assets(sha256);
            CREATE TABLE IF NOT EXISTS asset_lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_artifact_id TEXT,
                parent_path TEXT NOT NULL,
                parent_sha256 TEXT,
                child_artifact_id TEXT NOT NULL REFERENCES assets(artifact_id) ON DELETE CASCADE,
                child_path TEXT NOT NULL,
                child_sha256 TEXT,
                operation TEXT NOT NULL,
                modality TEXT NOT NULL,
                task_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(parent_path, child_artifact_id, operation)
            );
            CREATE INDEX IF NOT EXISTS ix_lineage_child
                ON asset_lineage(child_artifact_id);
            CREATE INDEX IF NOT EXISTS ix_lineage_parent_id
                ON asset_lineage(parent_artifact_id);
            CREATE INDEX IF NOT EXISTS ix_lineage_parent_path
                ON asset_lineage(parent_path);
            CREATE INDEX IF NOT EXISTS ix_lineage_parent_sha
                ON asset_lineage(parent_sha256);
            PRAGMA user_version = 3;
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
    include_local: bool = False,
) -> list[dict[str, Any]]:
    """List tasks, filtering by modality unless modality is None."""
    clauses: list[str] = []
    values: list[Any] = []
    if not include_local:
        clauses.append("base_url NOT IN ('local://process', 'local://ui')")
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
    category: str | None = None,
    source_kind: str | None = None,
    operation: str | None = None,
    metadata: Mapping[str, Any] | str | None = None,
    metadata_json: str | None = None,
) -> dict[str, Any]:
    if remote_index < 0:
        raise AsxError("Asset index must be non-negative", code="invalid_asset")
    if get_task(connection, local_task_id) is None:
        raise AsxError(f"Task not found: {local_task_id}", code="task_not_found")
    now = _timestamp(created_at)
    stable_id = artifact_id or _asset_id(local_task_id, remote_index)
    byte_count = size_bytes if size_bytes is not None else bytes
    resolved_source_kind = source_kind or "cloud_task"
    if metadata is not None and metadata_json is not None:
        raise ValueError("provide at most one metadata or metadata_json")
    serialized_metadata = (
        _json(metadata) if metadata is not None else (_json(metadata_json) if metadata_json is not None else None)
    )
    connection.execute(
        "INSERT INTO assets "
        "(artifact_id, local_task_id, remote_index, path, remote_url, mime, bytes, sha256, "
        "state, created_at, updated_at, category, source_kind, operation, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(local_task_id, remote_index) DO UPDATE SET "
        "path = excluded.path, remote_url = excluded.remote_url, mime = excluded.mime, "
        "bytes = excluded.bytes, "
        "sha256 = excluded.sha256, state = excluded.state, updated_at = excluded.updated_at, "
        "category = COALESCE(excluded.category, assets.category), "
        "source_kind = COALESCE(?, assets.source_kind), "
        "operation = COALESCE(excluded.operation, assets.operation), "
        "metadata_json = COALESCE(excluded.metadata_json, assets.metadata_json)",
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
            category,
            resolved_source_kind,
            operation,
            serialized_metadata,
            source_kind,
        ),
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM assets WHERE local_task_id = ? AND remote_index = ?",
        (local_task_id, remote_index),
    ).fetchone()
    if row is None:
        raise AsxError("Asset was not persisted", code="asset_state_write_failed")
    return _asset_row_dict(row)


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
    return [_asset_row_dict(row) for row in rows]


def get_asset_by_id(
    connection: sqlite3.Connection, artifact_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM assets WHERE artifact_id = ?", (artifact_id,)
    ).fetchone()
    return _asset_row_dict(row) if row is not None else None


def get_asset_by_path(
    connection: sqlite3.Connection, path: str | Path
) -> dict[str, Any] | None:
    resolved = _canonical_path(path)
    row = connection.execute(
        "SELECT * FROM assets WHERE path = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
        (resolved,),
    ).fetchone()
    return _asset_row_dict(row) if row is not None else None


def _ensure_synthetic_local_task(
    connection: sqlite3.Connection,
    *,
    local_task_id: str,
    operation: str,
    modality: str,
    source_kind: str,
    output_dir: str,
    model: str = "local",
    request: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> None:
    if get_task(connection, local_task_id) is not None:
        return
    now = _timestamp(created_at)
    base_url = "local://ui" if source_kind == "ui_export" else "local://process"
    idempotency_key = f"asx-{source_kind}-{local_task_id}"
    payload = _json(
        request
        if request is not None
        else {"source_kind": source_kind, "operation": operation, "modality": modality}
    )
    connection.execute(
        "INSERT OR IGNORE INTO tasks "
        "(local_id, remote_task_id, operation, model, status, idempotency_key, request_json, "
        "output_dir, remote_json, error_json, billing, created_at, updated_at, "
        "last_polled, base_url, modality) "
        "VALUES (?, NULL, ?, ?, 'succeeded', ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, ?, ?)",
        (
            local_task_id,
            operation,
            model,
            idempotency_key,
            payload,
            output_dir,
            now,
            now,
            base_url,
            modality,
        ),
    )


def _resolve_parent_entry(
    connection: sqlite3.Connection,
    *,
    parent_path: str | Path | None = None,
    parent_artifact_id: str | None = None,
) -> tuple[str | None, str, str | None] | None:
    if parent_artifact_id is not None:
        row = get_asset_by_id(connection, parent_artifact_id)
        if row is not None:
            p_path = str(row["path"])
            p_sha = row.get("sha256")
            if p_path and not p_sha and not p_path.startswith(("http://", "https://")):
                p_sha = compute_file_sha256(p_path)
            return str(row["artifact_id"]), p_path, p_sha
        if parent_path is None:
            return None

    assert parent_path is not None
    raw = str(parent_path).strip()
    if not raw or raw.casefold().startswith("data:"):
        return None

    if raw.casefold().startswith(("http://", "https://")):
        row = connection.execute(
            "SELECT * FROM assets WHERE remote_url = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (raw,),
        ).fetchone()
        if row is not None:
            return str(row["artifact_id"]), str(row["path"] or raw), row["sha256"]
        return None, raw, None

    resolved = _canonical_path(raw)
    computed_sha = compute_file_sha256(resolved)
    matched = connection.execute(
        "SELECT * FROM assets WHERE path = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
        (resolved,),
    ).fetchone()
    if matched is None and computed_sha:
        matched = connection.execute(
            "SELECT * FROM assets WHERE sha256 = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (computed_sha,),
        ).fetchone()
    if matched is not None:
        return (
            str(matched["artifact_id"]),
            resolved,
            computed_sha or matched["sha256"],
        )
    return None, resolved, computed_sha


def record_artifact_with_lineage(
    connection: sqlite3.Connection,
    *,
    file_path: str | Path,
    modality: str,
    operation: str,
    source_kind: str = "cloud_task",
    category: str | None = None,
    local_task_id: str | None = None,
    remote_index: int = 0,
    remote_url: str | None = None,
    mime: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    parent_paths: Sequence[str | Path] | None = None,
    parent_artifact_ids: Sequence[str] | None = None,
    task_id: str | None = None,
    model: str = "local",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Register an output asset in `assets` and record parent-child edges in `asset_lineage`."""
    resolved_child = _canonical_path(file_path)
    child_path_obj = Path(resolved_child)
    if size_bytes is None and child_path_obj.is_file():
        try:
            size_bytes = child_path_obj.stat().st_size
        except OSError:
            size_bytes = None
    if sha256 is None and child_path_obj.is_file():
        sha256 = compute_file_sha256(child_path_obj)
    if mime is None:
        guessed_mime, _ = mimetypes.guess_type(resolved_child)
        mime = guessed_mime

    resolved_category = category
    if not resolved_category:
        if operation in {"spritesheet", "frame_sequence", "render_sequence"}:
            resolved_category = "frame_sequence"
        else:
            resolved_category = modality

    effective_local_id = local_task_id or new_local_task_id(
        "local_ui" if source_kind == "ui_export" else "local_proc"
    )
    resolved_parents_preview = [
        _canonical_path(p)
        for p in (parent_paths or [])
        if str(p).strip() and not str(p).strip().casefold().startswith("data:")
    ]
    _ensure_synthetic_local_task(
        connection,
        local_task_id=effective_local_id,
        operation=operation,
        modality=modality,
        source_kind=source_kind,
        output_dir=str(child_path_obj.parent),
        model=model,
        request={
            "source_kind": source_kind,
            "operation": operation,
            "modality": modality,
            "parents": resolved_parents_preview,
            "metadata": dict(metadata) if metadata else {},
        },
        created_at=created_at,
    )

    child_asset = upsert_asset(
        connection,
        effective_local_id,
        remote_index,
        path=resolved_child,
        remote_url=remote_url,
        mime=mime,
        bytes=size_bytes,
        sha256=sha256,
        state="available",
        created_at=created_at,
        category=resolved_category,
        source_kind=source_kind,
        operation=operation,
        metadata=metadata,
    )
    child_artifact_id = str(child_asset["artifact_id"])
    effective_task_id = task_id or effective_local_id
    now = _timestamp(created_at)

    resolved_parents: list[tuple[str | None, str, str | None]] = []
    seen_parent_keys: set[str] = set()
    for p_id in parent_artifact_ids or []:
        entry = _resolve_parent_entry(connection, parent_artifact_id=p_id)
        if entry is not None and entry[1] not in seen_parent_keys:
            seen_parent_keys.add(entry[1])
            resolved_parents.append(entry)
    for p_raw in parent_paths or []:
        entry = _resolve_parent_entry(connection, parent_path=p_raw)
        if entry is not None and entry[1] not in seen_parent_keys:
            seen_parent_keys.add(entry[1])
            resolved_parents.append(entry)

    lineage_edges: list[dict[str, Any]] = []
    for p_artifact_id, p_path, p_sha in resolved_parents:
        connection.execute(
            "INSERT INTO asset_lineage "
            "(parent_artifact_id, parent_path, parent_sha256, "
            "child_artifact_id, child_path, child_sha256, "
            "operation, modality, task_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(parent_path, child_artifact_id, operation) DO UPDATE SET "
            "parent_artifact_id = COALESCE(excluded.parent_artifact_id, asset_lineage.parent_artifact_id), "
            "parent_sha256 = COALESCE(excluded.parent_sha256, asset_lineage.parent_sha256), "
            "child_path = excluded.child_path, "
            "child_sha256 = COALESCE(excluded.child_sha256, asset_lineage.child_sha256), "
            "task_id = COALESCE(excluded.task_id, asset_lineage.task_id)",
            (
                p_artifact_id,
                p_path,
                p_sha,
                child_artifact_id,
                resolved_child,
                sha256,
                operation,
                modality,
                effective_task_id,
                now,
            ),
        )
        edge_row = connection.execute(
            "SELECT * FROM asset_lineage "
            "WHERE parent_path = ? AND child_artifact_id = ? AND operation = ?",
            (p_path, child_artifact_id, operation),
        ).fetchone()
        if edge_row is not None:
            lineage_edges.append(dict(zip(edge_row.keys(), tuple(edge_row))))

    connection.commit()
    child_asset["lineage_parents"] = lineage_edges
    return child_asset


def list_lineage(
    connection: sqlite3.Connection,
    *,
    child_artifact_id: str | None = None,
    parent_artifact_id: str | None = None,
    parent_path: str | None = None,
    child_path: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if child_artifact_id is not None:
        clauses.append("child_artifact_id = ?")
        values.append(child_artifact_id)
    if parent_artifact_id is not None:
        clauses.append("parent_artifact_id = ?")
        values.append(parent_artifact_id)
    if parent_path is not None:
        clauses.append("parent_path = ?")
        values.append(_canonical_path(parent_path))
    if child_path is not None:
        clauses.append("child_path = ?")
        values.append(_canonical_path(child_path))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    bounded = max(1, min(int(limit), 2000))
    rows = connection.execute(
        f"SELECT * FROM asset_lineage{where} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*values, bounded),
    ).fetchall()
    return [dict(zip(row.keys(), tuple(row))) for row in rows]


def get_asset_lineage(
    connection: sqlite3.Connection, identifier: str | Path
) -> dict[str, Any]:
    """Return direct parents/children and full upstream/downstream DAG edges for an asset."""
    raw = str(identifier).strip()
    asset = get_asset_by_id(connection, raw)
    if asset is None:
        resolved_path = _canonical_path(raw)
        asset = get_asset_by_path(connection, resolved_path)
    else:
        resolved_path = str(asset["path"])

    def _direct_parents(target_id: str | None, target_path: str | None) -> list[dict[str, Any]]:
        if target_id:
            rows = connection.execute(
                "SELECT * FROM asset_lineage WHERE child_artifact_id = ? ORDER BY id",
                (target_id,),
            ).fetchall()
        elif target_path:
            rows = connection.execute(
                "SELECT * FROM asset_lineage WHERE child_path = ? ORDER BY id",
                (target_path,),
            ).fetchall()
        else:
            rows = []
        return [dict(zip(r.keys(), tuple(r))) for r in rows]

    def _direct_children(target_id: str | None, target_path: str | None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if target_id:
            clauses.append("parent_artifact_id = ?")
            params.append(target_id)
        if target_path:
            clauses.append("parent_path = ?")
            params.append(target_path)
        if not clauses:
            return []
        rows = connection.execute(
            f"SELECT * FROM asset_lineage WHERE {' OR '.join(clauses)} ORDER BY id",
            tuple(params),
        ).fetchall()
        return [dict(zip(r.keys(), tuple(r))) for r in rows]

    root_id = str(asset["artifact_id"]) if asset is not None else None
    direct_parents = _direct_parents(root_id, resolved_path)
    direct_children = _direct_children(root_id, resolved_path)

    ancestors: list[dict[str, Any]] = []
    seen_up_edges: set[int] = set()
    visited_up: set[tuple[str | None, str | None]] = {(root_id, resolved_path)}
    up_queue: deque[tuple[str | None, str | None]] = deque()
    for edge in direct_parents:
        eid = int(edge["id"])
        if eid not in seen_up_edges:
            seen_up_edges.add(eid)
            ancestors.append(edge)
        node = (edge.get("parent_artifact_id"), edge.get("parent_path"))
        if node not in visited_up:
            visited_up.add(node)
            up_queue.append(node)
    while up_queue:
        p_id, p_path = up_queue.popleft()
        for edge in _direct_parents(p_id, p_path):
            eid = int(edge["id"])
            if eid not in seen_up_edges:
                seen_up_edges.add(eid)
                ancestors.append(edge)
            next_node = (edge.get("parent_artifact_id"), edge.get("parent_path"))
            if next_node not in visited_up:
                visited_up.add(next_node)
                up_queue.append(next_node)

    descendants: list[dict[str, Any]] = []
    seen_down_edges: set[int] = set()
    visited_down: set[tuple[str | None, str | None]] = {(root_id, resolved_path)}
    down_queue: deque[tuple[str | None, str | None]] = deque()
    for edge in direct_children:
        eid = int(edge["id"])
        if eid not in seen_down_edges:
            seen_down_edges.add(eid)
            descendants.append(edge)
        c_id = edge.get("child_artifact_id")
        c_path = edge.get("child_path")
        node = (c_id, c_path)
        if node not in visited_down:
            visited_down.add(node)
            down_queue.append(node)
    while down_queue:
        c_id, c_path = down_queue.popleft()
        for edge in _direct_children(c_id, c_path):
            eid = int(edge["id"])
            if eid not in seen_down_edges:
                seen_down_edges.add(eid)
                descendants.append(edge)
            next_node = (edge.get("child_artifact_id"), edge.get("child_path"))
            if next_node not in visited_down:
                visited_down.add(next_node)
                down_queue.append(next_node)

    return {
        "asset": asset,
        "path": resolved_path,
        "parents": direct_parents,
        "children": direct_children,
        "ancestors": ancestors,
        "descendants": descendants,
    }


def list_all_assets(
    connection: sqlite3.Connection,
    *,
    state: str | None = "available",
) -> list[dict[str, Any]]:
    """List all indexed assets joined with their task metadata."""
    clauses: list[str] = []
    params: list[Any] = []
    if state is not None:
        clauses.append("a.state = ?")
        params.append(state)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        "SELECT a.*, t.remote_task_id, t.operation AS task_operation, t.model, "
        "t.modality, t.request_json, t.output_dir, t.base_url "
        "FROM assets AS a JOIN tasks AS t ON t.local_id = a.local_task_id"
        f"{where} ORDER BY a.updated_at DESC, a.rowid DESC",
        tuple(params),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = _asset_row_dict(row)
        if "request_json" in item:
            item["request"] = _decode(item["request_json"])
        results.append(item)
    return results


def relink_moved_asset(
    connection: sqlite3.Connection,
    artifact_id: str,
    new_path: str | Path,
    *,
    sha256: str | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update an asset's path and lineage path references inside an atomic transaction."""
    resolved_new = _canonical_path(new_path)
    now = utc_now()

    with connection:
        existing = connection.execute(
            "SELECT * FROM assets WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if existing is None:
            raise AsxError(f"Asset not found: {artifact_id}", code="asset_not_found")

        old_path = str(existing["path"])
        local_task_id = str(existing["local_task_id"])
        existing_meta = _decode(existing["metadata_json"])
        updated_meta: dict[str, Any] | None = None
        if manifest_path is not None:
            resolved_manifest = _canonical_path(manifest_path)
            updated_meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
            updated_meta["manifest_path"] = resolved_manifest

        if updated_meta is not None:
            connection.execute(
                "UPDATE assets SET path = ?, sha256 = COALESCE(?, sha256), "
                "metadata_json = ?, updated_at = ? WHERE artifact_id = ?",
                (resolved_new, sha256, _json(updated_meta), now, artifact_id),
            )
        else:
            connection.execute(
                "UPDATE assets SET path = ?, sha256 = COALESCE(?, sha256), "
                "updated_at = ? WHERE artifact_id = ?",
                (resolved_new, sha256, now, artifact_id),
            )

        connection.execute(
            "UPDATE asset_lineage SET child_path = ?, child_sha256 = COALESCE(?, child_sha256) "
            "WHERE child_artifact_id = ?",
            (resolved_new, sha256, artifact_id),
        )

        parent_edges = connection.execute(
            "SELECT id, child_artifact_id, operation FROM asset_lineage "
            "WHERE parent_artifact_id = ? OR (parent_artifact_id IS NULL AND parent_path = ?)",
            (artifact_id, old_path),
        ).fetchall()
        for edge in parent_edges:
            edge_id = int(edge["id"])
            duplicate = connection.execute(
                "SELECT id FROM asset_lineage "
                "WHERE parent_path = ? AND child_artifact_id = ? AND operation = ? AND id != ?",
                (resolved_new, edge["child_artifact_id"], edge["operation"], edge_id),
            ).fetchone()
            if duplicate is not None:
                connection.execute(
                    "UPDATE asset_lineage SET parent_artifact_id = COALESCE(parent_artifact_id, ?), "
                    "parent_sha256 = COALESCE(?, parent_sha256) WHERE id = ?",
                    (artifact_id, sha256, int(duplicate["id"])),
                )
                connection.execute("DELETE FROM asset_lineage WHERE id = ?", (edge_id,))
            else:
                connection.execute(
                    "UPDATE asset_lineage SET parent_artifact_id = ?, parent_path = ?, "
                    "parent_sha256 = COALESCE(?, parent_sha256) WHERE id = ?",
                    (artifact_id, resolved_new, sha256, edge_id),
                )

        sibling_rows = connection.execute(
            "SELECT path FROM assets WHERE local_task_id = ?", (local_task_id,)
        ).fetchall()
        new_parent_dir = str(Path(resolved_new).parent)
        if sibling_rows and all(
            str(Path(str(r["path"])).parent) == new_parent_dir for r in sibling_rows
        ):
            connection.execute(
                "UPDATE tasks SET output_dir = ?, updated_at = ? WHERE local_id = ?",
                (new_parent_dir, now, local_task_id),
            )

    refreshed = get_asset_by_id(connection, artifact_id)
    if refreshed is None:
        raise AsxError(f"Asset not found after relink: {artifact_id}", code="asset_not_found")
    return refreshed


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
    "compute_file_sha256",
    "connect_db",
    "create_task_intent",
    "ensure_batch_schema",
    "get_asset_by_id",
    "get_asset_by_path",
    "get_asset_lineage",
    "get_task",
    "init_schema",
    "list_all_assets",
    "list_assets",
    "list_lineage",
    "list_tasks",
    "new_local_task_id",
    "record_artifact_with_lineage",
    "relink_moved_asset",
    "task_paths",
    "update_task",
    "upsert_asset",
    "utc_now",
]
