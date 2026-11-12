"""Shared database. Every API operation holds one consistent transaction."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scopes(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, scope_id INTEGER NOT NULL UNIQUE REFERENCES scopes(id)
);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL,
 user_id INTEGER REFERENCES users(id), guest_scope INTEGER REFERENCES scopes(id)
);
CREATE INDEX IF NOT EXISTS session_expiry ON sessions(expires);
CREATE TABLE IF NOT EXISTS groups(
 id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 owner_id INTEGER NOT NULL REFERENCES users(id), scope_id INTEGER NOT NULL UNIQUE REFERENCES scopes(id),
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))
);
CREATE TABLE IF NOT EXISTS memberships(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 group_id INTEGER NOT NULL REFERENCES groups(id), joined_at TEXT NOT NULL, left_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_group ON memberships(user_id) WHERE left_at IS NULL;
CREATE INDEX IF NOT EXISTS user_memberships ON memberships(user_id);
CREATE INDEX IF NOT EXISTS group_members ON memberships(group_id, left_at);
CREATE TABLE IF NOT EXISTS options(
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id INTEGER NOT NULL REFERENCES scopes(id),
 name TEXT NOT NULL, name_key TEXT NOT NULL, weight REAL NOT NULL CHECK(weight >= 0 AND weight <= 1.000000000001),
 color TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), CHECK(active=0 OR weight>0)
);
CREATE UNIQUE INDEX IF NOT EXISTS option_names ON options(scope_id,name_key) WHERE active=1;
CREATE TABLE IF NOT EXISTS lunches(
 scope_id INTEGER NOT NULL REFERENCES scopes(id), day TEXT NOT NULL,
 option_id INTEGER NOT NULL REFERENCES options(id), option_name TEXT NOT NULL, color TEXT NOT NULL,
 rerolls INTEGER NOT NULL CHECK(rerolls BETWEEN 0 AND 3), chosen_at TEXT NOT NULL,
 PRIMARY KEY(scope_id,day)
);
CREATE TABLE IF NOT EXISTS membership_lunches(
 membership_id INTEGER NOT NULL REFERENCES memberships(id), day TEXT NOT NULL,
 option_name TEXT NOT NULL, color TEXT NOT NULL, rerolls INTEGER NOT NULL, chosen_at TEXT NOT NULL,
 PRIMARY KEY(membership_id,day)
);
CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
"""


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("网页数据库版本不兼容。")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()


def new_scope(db):
    return db.execute("INSERT INTO scopes DEFAULT VALUES").lastrowid
