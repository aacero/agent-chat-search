import os
import time
import sqlite3
import threading
import subprocess
import tempfile
import shlex
from pathlib import Path
from typing import Dict, Any, List, Optional

from .collector import FleetCollector, get_current_hostname
from .schema import init_db
from .config import get_config

# Lock to ensure only one sync job runs at a time
_SYNC_LOCK = threading.Lock()
_SYNC_IN_PROGRESS = False

def is_sync_running() -> bool:
    return _SYNC_IN_PROGRESS

def get_sync_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT host, last_synced_at, session_count, message_count, status
        FROM sync_meta
        ORDER BY host ASC
    """)
    rows = cursor.fetchall()

    now = time.time()
    by_host = {}
    overall_last_synced = 0.0

    for r in rows:
        host = r[0]
        last_sync = r[1] or 0.0
        if last_sync > overall_last_synced:
            overall_last_synced = last_sync

        age_s = int(now - last_sync) if last_sync > 0 else None
        by_host[host] = {
            "last_synced_at": last_sync,
            "age_seconds": age_s,
            "sessions": r[2],
            "messages": r[3],
            "status": r[4]
        }

    cfg_hosts = get_config().hosts
    for h in cfg_hosts:
        if h not in by_host:
            by_host[h] = {
                "last_synced_at": 0.0,
                "age_seconds": None,
                "sessions": 0,
                "messages": 0,
                "status": "not_synced"
            }

    overall_age = int(now - overall_last_synced) if overall_last_synced > 0 else None

    return {
        "sync_in_progress": _SYNC_IN_PROGRESS,
        "last_synced_at": overall_last_synced,
        "age_seconds": overall_age,
        "by_host": by_host
    }

def sync_single_host(dest_conn: sqlite3.Connection, host: str, current_host: str, collector: FleetCollector) -> Dict[str, Any]:
    now = time.time()
    if host == current_host:
        try:
            res = collector.sync_local(dest_conn)
            cur = dest_conn.cursor()
            cur.execute("SELECT count(*) FROM sessions WHERE host = ?", (host,))
            sess_cnt = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM messages WHERE host = ?", (host,))
            msg_cnt = cur.fetchone()[0]

            dest_conn.execute("""
                INSERT OR REPLACE INTO sync_meta(host, last_synced_at, session_count, message_count, status)
                VALUES (?, ?, ?, ?, 'ok')
            """, (host, now, sess_cnt, msg_cnt))
            dest_conn.commit()
            return {"host": host, "status": "ok", "sessions": sess_cnt, "messages": msg_cnt}
        except Exception as e:
            dest_conn.execute("""
                INSERT OR REPLACE INTO sync_meta(host, last_synced_at, session_count, message_count, status)
                VALUES (?, ?, 0, 0, ?)
            """, (host, now, f"error: {str(e)[:50]}"))
            dest_conn.commit()
            return {"host": host, "status": "error", "error": str(e)}

    # Remote host via SSH / rsync
    ssh_opts = f"ssh -i {collector.ssh_key} -o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=accept-new"

    # 1. Trigger local index on remote host and checkpoint WAL
    remote_cmd = (
        "~/.local/bin/agent-search sync --local-only && "
        "python3 -c \"import sqlite3, os; p=os.path.expanduser('~/.local/share/agent-chat-search/fleet_chats.db'); "
        "c=sqlite3.connect(p); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()\""
    )
    cmd_index = [
        "ssh", "-i", collector.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{collector.ssh_user}@{host}",
        remote_cmd
    ]
    try:
        p1 = subprocess.run(cmd_index, capture_output=True, timeout=180)
        if p1.returncode != 0:
            dest_conn.execute("""
                INSERT OR REPLACE INTO sync_meta(host, last_synced_at, session_count, message_count, status)
                VALUES (?, ?, 0, 0, 'unreachable')
            """, (host, now))
            dest_conn.commit()
            return {"host": host, "status": "unreachable"}
    except Exception:
        dest_conn.execute("""
            INSERT OR REPLACE INTO sync_meta(host, last_synced_at, session_count, message_count, status)
            VALUES (?, ?, 0, 0, 'timed_out')
        """, (host, now))
        dest_conn.commit()
        return {"host": host, "status": "timed_out"}

    # 2. Pull SQLite database delta to secure temp directory
    with tempfile.TemporaryDirectory(prefix="fleet_sync_") as tmp_dir:
        tmp_path = os.path.join(tmp_dir, f"{host}_fleet_sync.db")
        cmd_pull = [
            "rsync", "-e", ssh_opts, "-avz",
            f"{collector.ssh_user}@{host}:~/.local/share/agent-chat-search/fleet_chats.db",
            tmp_path
        ]
        try:
            p2 = subprocess.run(cmd_pull, capture_output=True, timeout=180)
            if p2.returncode != 0 or not os.path.exists(tmp_path):
                return {"host": host, "status": "pull_failed"}

            # 3. Merge into master database with strict host filtering
            safe_tmp_path = tmp_path.replace('"', '""')
            attached = False
            try:
                dest_conn.execute(f'ATTACH DATABASE "{safe_tmp_path}" AS remote_db;')
                attached = True
                dest_conn.execute('INSERT OR REPLACE INTO sessions SELECT * FROM remote_db.sessions WHERE host = ?;', (host,))
                dest_conn.execute('DELETE FROM messages WHERE host = ?;', (host,))
                dest_conn.execute("""
                    INSERT INTO messages(session_uid, host, agent, role, content, timestamp)
                    SELECT session_uid, host, agent, role, content, timestamp
                    FROM remote_db.messages
                    WHERE host = ?;
                """, (host,))
                dest_conn.commit()
            finally:
                if attached:
                    try:
                        dest_conn.execute('DETACH DATABASE remote_db;')
                    except Exception:
                        pass

            cur = dest_conn.cursor()
            cur.execute("SELECT count(*) FROM sessions WHERE host = ?", (host,))
            sess_cnt = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM messages WHERE host = ?", (host,))
            msg_cnt = cur.fetchone()[0]

            dest_conn.execute("""
                INSERT OR REPLACE INTO sync_meta(host, last_synced_at, session_count, message_count, status)
                VALUES (?, ?, ?, ?, 'ok')
            """, (host, now, sess_cnt, msg_cnt))
            dest_conn.commit()

            return {"host": host, "status": "ok", "sessions": sess_cnt, "messages": msg_cnt}
        except Exception as e:
            return {"host": host, "status": "merge_error", "error": str(e)}

def run_fleet_sync(db_path: Optional[str] = None, hosts: Optional[List[str]] = None) -> Dict[str, Any]:
    global _SYNC_IN_PROGRESS
    if not _SYNC_LOCK.acquire(blocking=False):
        return {"status": "already_running"}

    _SYNC_IN_PROGRESS = True
    try:
        conn = init_db(db_path)
        collector = FleetCollector()
        current_host = get_current_hostname()
        target_hosts = hosts if hosts is not None else get_config().hosts

        results = {}
        for h in target_hosts:
            results[h] = sync_single_host(conn, h, current_host, collector)

        return {"status": "completed", "results": results}
    finally:
        _SYNC_IN_PROGRESS = False
        _SYNC_LOCK.release()
