"""Who owns the local task ledger, and the explicit move of unowned records into it.

Cloud tasks, batches and charge confirmations belong to one identity (site + Agent + account) and live
in that identity's own ledger (``config.state_path``). Releases before this split kept everything in one
ledger per OS user without recording an owner. Those records are never picked up implicitly: the user
has to confirm they are theirs with ``ledger migrate --confirm-ownership``, which copies them and leaves
the source file as it is. Tasks copied this way carry no charge confirmation, so a task that never
reached the gateway is not resubmitted; it has to be requested and confirmed again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .auth import ledger_identity
from .config import load_client, state_path, unbound_state_path
from .errors import AsxError
from .state import claim_ledger, cloud_record_counts, connect_db, init_schema, ledger_owner


def _open_readonly(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise AsxError(f"无法读取任务账本：{path}", code="ledger_unreadable", details={"path": str(path)}) from exc
    connection.row_factory = sqlite3.Row
    return connection


def _source_summary(source: sqlite3.Connection, gateway: str | None) -> dict[str, Any]:
    counts = cloud_record_counts(source)
    summary: dict[str, Any] = {"cloud_tasks": counts["tasks"], "batches": counts["batches"]}
    if counts["tasks"]:
        cloud = "base_url NOT IN ('local://process', 'local://ui')"
        summary["never_submitted"] = int(source.execute(
            f"SELECT COUNT(*) FROM tasks WHERE {cloud} AND remote_task_id IS NULL"
        ).fetchone()[0])
        if gateway is not None:
            summary["other_gateway"] = int(source.execute(
                f"SELECT COUNT(*) FROM tasks WHERE {cloud} AND base_url != ?", (gateway,)
            ).fetchone()[0])
    return summary


def ledger_status() -> dict[str, Any]:
    identity = ledger_identity()
    path = state_path()
    connection = connect_db()
    try:
        current = {"path": str(path), "owner": ledger_owner(connection), **cloud_record_counts(connection)}
    finally:
        connection.close()
    payload: dict[str, Any] = {"ok": True, "identity": identity, "ledger": current}
    unbound = unbound_state_path()
    if unbound.is_file() and unbound.resolve() != path.resolve():
        source = _open_readonly(unbound)
        try:
            summary = _source_summary(source, None)
            claimed = ledger_owner(source) is not None
        finally:
            source.close()
        if not claimed and (summary["cloud_tasks"] or summary["batches"]):
            payload["unowned_ledger"] = {
                "path": str(unbound),
                **summary,
                "message": "旧版本留下的云端任务没有记录归属，当前身份不会读取或恢复它们。"
                "确认属于当前账号后，可用 ledger migrate --confirm-ownership 迁入。",
                "next_action_args": ["ledger", "migrate", "--source", str(unbound), "--confirm-ownership"],
            }
    return payload


def migrate_ledger(source_path: str | None, *, confirm_ownership: bool) -> dict[str, Any]:
    client = load_client()
    identity = client.session.identity
    source_file = Path(source_path).expanduser() if source_path else unbound_state_path()
    target_file = state_path()
    if not source_file.is_file():
        raise AsxError(f"没有找到要迁移的任务账本：{source_file}", code="ledger_not_found",
                       details={"source": str(source_file)})
    if source_file.resolve() == target_file.resolve():
        raise AsxError("来源就是当前身份的账本，无需迁移。", code="ledger_same_file",
                       details={"source": str(source_file)})
    source = _open_readonly(source_file)
    try:
        owner = ledger_owner(source)
        if owner is not None and owner != identity:
            raise AsxError(
                "来源账本属于另一个身份，不能迁入当前身份。",
                code="ledger_owner_mismatch",
                exit_code=2,
                details={"source": str(source_file), "ledger_origin": owner["origin"],
                         "ledger_agent_id": owner["agent_id"], "current_identity": identity},
            )
        summary = _source_summary(source, client.base_url)
        if not confirm_ownership:
            raise AsxError(
                "迁移前需要用户确认：来源账本里的任务都是当前账号在当前站点提交的。确认后加 --confirm-ownership。"
                "来源文件不会被修改；从未提交成功的任务迁入后也不会自动重新提交。",
                code="ownership_confirmation_required",
                exit_code=2,
                details={"source": str(source_file), "target": str(target_file), "identity": identity,
                         "records": summary,
                         "confirm_args": ["ledger", "migrate", "--source", str(source_file), "--confirm-ownership"]},
            )
        target = connect_db()
        try:
            tables = {str(row[0]) for row in target.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if any(
                target.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in ("tasks", "assets", "batches", "charge_consents") if table in tables
            ):
                raise AsxError(
                    "当前身份的账本里已经有记录，不会用来源账本覆盖。迁移只能在当前身份还没有任何任务时进行。",
                    code="ledger_not_empty",
                    exit_code=2,
                    details={"target": str(target_file)},
                )
            source.backup(target)
            init_schema(target)
            claim_ledger(target, identity, migrated_from=str(source_file))
            target.commit()
        finally:
            target.close()
    finally:
        source.close()
    return {
        "ok": True,
        "migrated": True,
        "source": str(source_file),
        "source_kept": True,
        "target": str(target_file),
        "identity": identity,
        "records": summary,
        "note": "从未提交成功的任务没有当前身份的积分确认记录，不会被 task recover 重新提交；需要时请重新发起并确认报价。"
        "提交到其他网关的任务不会在当前网关上继续。",
    }
