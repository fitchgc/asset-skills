from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import state_path
from .constants import UTC
from .errors import AsxError


def recent_payload(
    query: str | None = None,
    limit: int = 20,
    *,
    latest: bool = False,
    modality: str | None = None,
) -> dict[str, Any]:
    from .state import connect_db as connect_ledger_db

    connection = connect_ledger_db()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if modality is not None:
            clauses.append("t.modality = ?")
            params.append(modality)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            clauses.append(
                "(a.artifact_id LIKE ? OR t.remote_task_id LIKE ? OR a.path LIKE ? "
                "OR t.request_json LIKE ? OR t.model LIKE ?)"
            )
            params.extend([pattern] * 5)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            "SELECT a.*, t.remote_task_id, t.request_json, t.model, t.operation, t.modality, t.updated_at AS task_updated_at "
            "FROM assets AS a JOIN tasks AS t ON t.local_id = a.local_task_id"
            f"{where} ORDER BY a.updated_at DESC, a.remote_index LIMIT ?",
            (*params, 1 if latest else max(1, min(int(limit), 1000))),
        ).fetchall()
        items = []
        for row in rows:
            path = str(row["path"])
            try:
                request = json.loads(row["request_json"])
            except (TypeError, json.JSONDecodeError):
                request = {}
            inp = request.get("input", {}) if isinstance(request, dict) else {}
            prompt = inp.get("prompt") or inp.get("text") if isinstance(inp, dict) else None
            items.append(
                {
                    "artifact_id": row["artifact_id"],
                    "local_id": row["local_task_id"],
                    "task_id": row["remote_task_id"],
                    "position": row["remote_index"],
                    "path": path,
                    "name": Path(path).name,
                    "prompt": prompt,
                    "model": row["model"],
                    "operation": row["operation"],
                    "modality": row["modality"] if "modality" in row.keys() else "image",
                    "created_at": row["created_at"],
                    "missing": not Path(path).is_file(),
                }
            )
        return {"ok": True, "schema_version": 2, "artifacts": items}
    finally:
        connection.close()
