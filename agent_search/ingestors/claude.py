import sqlite3
import os
import json
import glob
from pathlib import Path
from typing import Optional
from .base import BaseIngestor

class ClaudeCodeIngestor(BaseIngestor):
    def __init__(self, host: str, base_dir: Optional[str] = None):
        super().__init__(host)
        self.base_dir = base_dir or os.path.expanduser("~/.claude")

    def ingest(self, dest_conn: sqlite3.Connection) -> int:
        if not os.path.exists(self.base_dir):
            return 0

        session_files = glob.glob(os.path.join(self.base_dir, "sessions", "*.json"))
        transcript_files = glob.glob(os.path.join(self.base_dir, "projects", "*", "*.jsonl"))
        transcript_files.extend(glob.glob(os.path.join(self.base_dir, "*.jsonl")))

        messages_indexed = 0

        # Ingest session metadata cache
        sessions_meta = {}
        for sf in session_files:
            try:
                with open(sf, "r", encoding="utf-8", errors="replace") as fp:
                    data = json.load(fp)

                sess_id = data.get("sessionId") or Path(sf).stem
                session_uid = f"{self.host}:claude:{sess_id}"
                cwd = data.get("cwd", "")
                started_at = (data.get("startedAt", 0) / 1000.0) if data.get("startedAt") else 0.0
                title = data.get("name") or "Claude Code Session"

                sessions_meta[sess_id] = {
                    "session_uid": session_uid,
                    "title": title,
                    "cwd": cwd,
                    "started_at": started_at,
                    "path": sf
                }
            except Exception:
                continue

        # Ingest transcripts
        for tf in transcript_files:
            try:
                sess_id = Path(tf).stem
                meta = sessions_meta.get(sess_id, {})
                session_uid = meta.get("session_uid") or f"{self.host}:claude:{sess_id}"
                title = meta.get("title") or "Claude Code Session"
                cwd = meta.get("cwd") or ""
                started_at = meta.get("started_at") or 0.0

                batch = []
                with open(tf, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            role = record.get("role", "assistant")
                            content = record.get("content", "")
                            if isinstance(content, list):
                                content = "\n".join(
                                    item.get("text", "") if isinstance(item, dict) else str(item)
                                    for item in content
                                )
                            ts = record.get("timestamp", 0.0)
                            if content and content.strip():
                                batch.append((
                                    session_uid,
                                    self.host,
                                    "claude",
                                    role,
                                    self.clean_text(content),
                                    ts
                                ))
                        except Exception:
                            continue

                if batch:
                    dest_conn.execute("""
                        INSERT INTO sessions(uid, host, agent, native_id, title, project, model, started_at, message_count, source_path)
                        VALUES (?, ?, 'claude', ?, ?, ?, 'claude', ?, ?, ?)
                        ON CONFLICT(uid) DO UPDATE SET
                            title = excluded.title,
                            project = excluded.project,
                            message_count = excluded.message_count,
                            source_path = excluded.source_path
                    """, (
                        session_uid,
                        self.host,
                        sess_id,
                        title,
                        cwd,
                        started_at,
                        len(batch),
                        tf
                    ))
                    dest_conn.execute("DELETE FROM messages WHERE session_uid = ?", (session_uid,))
                    dest_conn.executemany("""
                        INSERT INTO messages(session_uid, host, agent, role, content, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, batch)
                    messages_indexed += len(batch)
            except Exception:
                continue

        # Clean up any ghost sessions with 0 messages
        dest_conn.execute("""
            DELETE FROM sessions WHERE agent = 'claude' AND host = ? AND message_count = 0
        """, (self.host,))

        dest_conn.commit()
        return messages_indexed
