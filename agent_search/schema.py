import sqlite3
import os
from typing import Optional
from pathlib import Path
from .config import get_config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    uid TEXT PRIMARY KEY,               -- {host}:{agent}:{native_id}
    host TEXT NOT NULL,
    agent TEXT NOT NULL,                -- hermes, agy, openclaw, codex, claude
    native_id TEXT NOT NULL,
    title TEXT,
    project TEXT,                       -- project path / cwd
    model TEXT,
    started_at REAL,
    message_count INTEGER DEFAULT 0,
    source_path TEXT,
    indexed_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uid TEXT NOT NULL,
    host TEXT NOT NULL,
    agent TEXT NOT NULL,
    role TEXT NOT NULL,                 -- user, assistant, system, tool
    content TEXT NOT NULL,
    timestamp REAL,
    FOREIGN KEY(session_uid) REFERENCES sessions(uid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_host ON sessions(host);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_uid);
CREATE INDEX IF NOT EXISTS idx_messages_host ON messages(host);

-- Standalone FTS5 table for fast snippet generation and ranking
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    title,
    project,
    host UNINDEXED,
    agent UNINDEXED,
    role UNINDEXED,
    session_uid UNINDEXED,
    msg_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);

-- Triggers to keep FTS index updated
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, title, project, host, agent, role, session_uid, msg_id)
    SELECT
        new.id,
        new.content,
        s.title,
        s.project,
        new.host,
        new.agent,
        new.role,
        new.session_uid,
        new.id
    FROM sessions s WHERE s.uid = new.session_uid;
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE OF title, project ON sessions
WHEN old.title IS NOT new.title OR old.project IS NOT new.project
BEGIN
    UPDATE messages_fts SET
        title = new.title,
        project = new.project
    WHERE session_uid = new.uid;
END;

CREATE TABLE IF NOT EXISTS sync_meta (
    host TEXT PRIMARY KEY,
    last_synced_at REAL,
    session_count INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    status TEXT
);
"""

def get_db_path() -> str:
    return get_config().db_path

def reconcile_schema_and_triggers(conn: sqlite3.Connection):
    """
    Reconciles existing database triggers to ensure optimal FTS5 performance:
    - messages_ai must explicitly write rowid = new.id.
    - messages_ad must delete by rowid = old.id (O(1) B-tree lookup), not unindexed msg_id.
    - sessions_au must include WHEN condition to prevent table scans on unindexed virtual columns.
    """
    cur = conn.cursor()
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
    triggers = {row[0]: row[1] or "" for row in cur.fetchall()}

    # Reconcile messages_ad
    if "messages_ad" in triggers:
        sql = triggers["messages_ad"]
        if "rowid" not in sql:
            conn.execute("DROP TRIGGER IF EXISTS messages_ad;")
            conn.execute("""
                CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
                    DELETE FROM messages_fts WHERE rowid = old.id;
                END;
            """)

    # Reconcile messages_ai
    if "messages_ai" in triggers:
        sql = triggers["messages_ai"]
        if "rowid" not in sql.split("INSERT INTO messages_fts", 1)[-1].split("SELECT", 1)[0]:
            conn.execute("DROP TRIGGER IF EXISTS messages_ai;")
            conn.execute("""
                CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content, title, project, host, agent, role, session_uid, msg_id)
                    SELECT
                        new.id,
                        new.content,
                        s.title,
                        s.project,
                        new.host,
                        new.agent,
                        new.role,
                        new.session_uid,
                        new.id
                    FROM sessions s WHERE s.uid = new.session_uid;
                END;
            """)

    # Reconcile sessions_au
    if "sessions_au" in triggers:
        sql = triggers["sessions_au"]
        if "WHEN" not in sql.upper():
            conn.execute("DROP TRIGGER IF EXISTS sessions_au;")
            conn.execute("""
                CREATE TRIGGER sessions_au AFTER UPDATE OF title, project ON sessions
                WHEN old.title IS NOT new.title OR old.project IS NOT new.project
                BEGIN
                    UPDATE messages_fts SET
                        title = new.title,
                        project = new.project
                    WHERE session_uid = new.uid;
                END;
            """)

    conn.commit()

def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    if not db_path:
        db_path = get_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA recursive_triggers=ON;")
    conn.executescript(SCHEMA_SQL)
    reconcile_schema_and_triggers(conn)
    conn.commit()
    return conn
