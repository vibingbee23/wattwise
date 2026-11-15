"""Process-local drafts and explicitly saved, durable public snapshots."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import json
import os
import secrets
import sqlite3
import time

from .models import Comparison

DEFAULTS = {
    'appliances': [
        dict(name='Bosch Series 6', category='Washing machine', price=649, energy=49, basis='100cycles', usage=4, label='A'),
        dict(name='Beko SteamCure', category='Washing machine', price=399, energy=76, basis='100cycles', usage=4, label='C'),
    ], 'rate': .30, 'currency': 'EUR', 'years': 10,
}
SESSION_TTL = 24 * 60 * 60
SESSIONS: dict[str, dict] = {}


def get_session(cookie):
    now = time.monotonic()
    for key in list(SESSIONS):
        if SESSIONS[key]['expires'] < now:
            del SESSIONS[key]
    fresh = cookie not in SESSIONS
    key = secrets.token_urlsafe(32) if fresh else cookie
    if fresh:
        SESSIONS[key] = {'comparison': deepcopy(DEFAULTS), 'csrf': secrets.token_urlsafe(32)}
    SESSIONS[key]['expires'] = now + SESSION_TTL
    return key, SESSIONS[key], fresh


@contextmanager
def database():
    path = Path(os.environ.get('WATTWISE_DB', '.data/wattwise.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, title TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            yield db
    finally:
        db.close()


def save_snapshot(comparison, title):
    key = secrets.token_urlsafe(18)
    payload = Comparison.model_validate(comparison).model_dump_json()
    with database() as db:
        db.execute('INSERT INTO snapshots(id,title,payload) VALUES (?,?,?)', (key, title, payload))
    return key


def get_snapshot(key):
    with database() as db:
        row = db.execute('SELECT title,payload,created FROM snapshots WHERE id=?', (key,)).fetchone()
    return {'id': key, 'title': row[0], 'comparison': json.loads(row[1]), 'created': row[2]} if row else None


def list_snapshots():
    with database() as db:
        return [dict(id=r[0], title=r[1], created=r[2]) for r in db.execute('SELECT id,title,created FROM snapshots ORDER BY rowid DESC LIMIT 50')]
