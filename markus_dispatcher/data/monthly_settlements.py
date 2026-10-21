from __future__ import annotations

from datetime import datetime
import json
import re

from markus_dispatcher.data import db


def month_of(value: str) -> str | None:
    try:
        return datetime.strptime(value, '%d.%m.%Y %H:%M').strftime('%Y-%m')
    except (ValueError, TypeError):
        return None


def validate_month(month: str) -> None:
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        raise ValueError('Nieprawidłowy miesiąc rozliczenia.')


def _schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS private_monthly_sheets (month TEXT PRIMARY KEY, target TEXT NOT NULL)')


def get(month: str) -> dict:
    validate_month(month)
    with db.get_conn() as conn:
        _schema(conn)
        row = conn.execute('SELECT target FROM private_monthly_sheets WHERE month=?', (month,)).fetchone()
    return json.loads(row['target']) if row else {}


def assign(month: str, file_id: str, tabs: list[dict]) -> None:
    validate_month(month)
    with db.get_conn() as conn:
        _schema(conn)
        row = conn.execute('SELECT target FROM private_monthly_sheets WHERE month=?', (month,)).fetchone()
        previous = json.loads(row['target']) if row else {}
        cleaned = [{'sheetId': int(tab['sheetId']), 'title': str(tab['title'])} for tab in tabs]
        ids = {tab['sheetId'] for tab in cleaned}
        drivers = previous.get('drivers', {}) if previous.get('file_id') == file_id else {}
        drivers = {key: sid for key, sid in drivers.items() if sid in ids}
        value = dict(file_id=file_id, tabs=cleaned, drivers=drivers)
        conn.execute('INSERT INTO private_monthly_sheets(month,target) VALUES (?,?) ON CONFLICT(month) DO UPDATE SET target=excluded.target',
                     (month, json.dumps(value, ensure_ascii=False)))


def bind_driver(month: str, driver_id: int, file_id: str, sheet_id: int) -> None:
    with db.get_conn() as conn:
        _schema(conn)
        row = conn.execute('SELECT target FROM private_monthly_sheets WHERE month=?', (month,)).fetchone()
        target = json.loads(row['target']) if row else {}
        if target.get('file_id') != file_id or sheet_id not in {t['sheetId'] for t in target.get('tabs', [])}:
            raise ValueError('Arkusz miesiąca został zmieniony. Wybierz ponownie zakładkę kierowcy.')
        target.setdefault('drivers', {})[str(driver_id)] = sheet_id
        conn.execute('UPDATE private_monthly_sheets SET target=? WHERE month=?', (json.dumps(target, ensure_ascii=False), month))


def jobs_for_month(driver_id: int, month: str):
    return [job for job in db.list_jobs_for_driver(driver_id, chronological=True)
            if month_of(job.unload_datetime) == month]
