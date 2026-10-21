"""Private local settlement state; intentionally outside JobData and Firebase payloads."""
from __future__ import annotations

import json
import uuid

from markus_dispatcher.data import db


def _schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS private_settlements (
        job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        export_id TEXT NOT NULL UNIQUE,
        draft TEXT NOT NULL DEFAULT '{}',
        binding TEXT NOT NULL DEFAULT '{}'
    )''')


def load(job_id: int) -> dict:
    with db.get_conn() as conn:
        _schema(conn)
        conn.execute('INSERT OR IGNORE INTO private_settlements(job_id, export_id) VALUES (?,?)',
                     (job_id, str(uuid.uuid4())))
        row = conn.execute('SELECT * FROM private_settlements WHERE job_id=?', (job_id,)).fetchone()
    return dict(export_id=row['export_id'], draft=json.loads(row['draft']), binding=json.loads(row['binding']))


def save_draft(job_id: int, draft: dict) -> None:
    load(job_id)
    with db.get_conn() as conn:
        conn.execute('UPDATE private_settlements SET draft=? WHERE job_id=?',
                     (json.dumps(draft, ensure_ascii=False), job_id))


def save_binding(job_id: int, binding: dict) -> None:
    load(job_id)
    with db.get_conn() as conn:
        conn.execute('UPDATE private_settlements SET binding=? WHERE job_id=?',
                     (json.dumps(binding, ensure_ascii=False), job_id))
