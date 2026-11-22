"""Identity-local admission control, keyed by the task's original output directory."""
from __future__ import annotations

import json
import re
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from .errors import AsxError
from .state import connect_db


def amount(value):
    if not re.fullmatch(r"[0-9]{1,17}(?:\.[0-9]{1,10})?", str(value)):
        raise AsxError("预算金额必须是非负十进制金额", code="project_amount_unknown")
    return Decimal(str(value))


def lock(db):
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE IF NOT EXISTS project_limits(root TEXT PRIMARY KEY, currency TEXT, cap TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS project_submissions(idempotency_key TEXT PRIMARY KEY)")


def entries(db, root, currency):
    spent = Decimal(0)
    held = Decimal(0)
    tasks = []
    for row in db.execute('''SELECT t.*, c.consent_json, s.idempotency_key AS submitted FROM tasks t
            JOIN charge_consents c USING(idempotency_key) LEFT JOIN project_submissions s USING(idempotency_key)'''):
        if not Path(row['output_dir']).resolve().is_relative_to(root):
            continue
        consent = json.loads(row['consent_json'])
        remote = json.loads(row['remote_json'] or '{}') or {}
        billing = remote.get('billing') or json.loads(row['billing'] or '{}') or {}
        if not (row['submitted'] or row['remote_task_id'] or row['status'] == 'unknown'):
            continue
        paid = billing.get('charge_status') == 'paid'
        released = (row['status'] in {'failed', 'canceled', 'timeout', 'blocked'} and
                    remote.get('status') == row['status'] and not paid)
        raw = billing.get('actual_amount') if paid else consent.get('unit_amount')
        unit = billing.get('currency') if paid else consent.get('currency')
        if not released and unit != currency:
            raise AsxError('任务的计价单位与项目预算不一致或缺失', code='project_currency_mismatch')
        value = Decimal(0) if released else amount(raw)
        if paid:
            spent += value
        else:
            held += value
        tasks.append({'local_id': row['local_id'], 'task_id': row['remote_task_id'], 'status': row['status'],
                      'quote': consent.get('unit_amount'), 'confirmation': consent.get('confirmation'),
                      'actual': str(value) if paid else None, 'reserved': str(0 if paid else value)})
    return spent, held, tasks


def configure(root, limit, currency):
    root = Path(root).expanduser().resolve()
    with closing(connect_db()) as db, db:
        lock(db)
        current = db.execute('SELECT * FROM project_limits WHERE root=?', (str(root),)).fetchone()
        if current:
            if limit is not None and currency != current['currency']:
                raise AsxError('项目预算单位不能更改', code='project_currency_mismatch')
            currency = current['currency']
        cap = amount(limit) if limit is not None else amount(current['cap']) if current else None
        ready = db.execute("SELECT 1 FROM sqlite_master WHERE name='charge_consents'").fetchone()
        spent, held, tasks = entries(db, root, currency) if ready else (Decimal(0), Decimal(0), [])
        if limit is not None:
            if cap < spent + held:
                raise AsxError('上限小于项目已用和预占之和', code='project_limit_exceeded')
            db.execute('INSERT OR REPLACE INTO project_limits VALUES(?,?,?)', (str(root), currency, str(cap)))
        return {'ok': True, 'root': str(root), 'currency': currency, 'limit': str(cap) if cap is not None else None,
                'actual': str(spent), 'reserved': str(held), 'tasks': tasks}


def reserve(db, key):
    # Commit before network I/O. Concurrent submissions see the hold even after a client crash.
    with db:
        lock(db)
        task = db.execute('SELECT output_dir FROM tasks WHERE idempotency_key=?', (key,)).fetchone()
        if not task:
            raise AsxError('缺少任务记录', code='task_not_found')
        existing = db.execute('SELECT 1 FROM project_submissions WHERE idempotency_key=?', (key,)).fetchone()
        if existing:
            return
        db.execute('INSERT INTO project_submissions VALUES(?)', (key,))
        directory = Path(task['output_dir']).resolve()
        for project in db.execute('SELECT * FROM project_limits'):
            root = Path(project['root'])
            if directory.is_relative_to(root):
                spent, held, _ = entries(db, root, project['currency'])
                if spent + held > amount(project['cap']):
                    raise AsxError('本次报价超过项目剩余预算', code='project_limit_exceeded')
