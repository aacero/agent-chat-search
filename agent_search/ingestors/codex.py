import sqlite3
import os
import json
import glob
from pathlib import Path
from typing import Optional
from .base import BaseIngestor

class CodexIngestor(BaseIngestor):
    def __init__(self, host: str, base_dir: Optional[str] = None):
        super().__init__(host)
        self.base_dir = base_dir or os.path.expanduser("~/.codex")

    def ingest(self, dest_conn: sqlite3.Connection) -> int:
        if not os.path.exists(self.base_dir):
            return 0

        # Look for state_*.sqlite databases
        db_paths = glob.glob(os.path.join(self.base_dir, "state_*.sqlite"))
        if not db_paths:
            return 0

        messages_indexed = 0

        for db_path in db_paths:
            try:
                src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                src_conn.row_factory = sqlite3.Row
                src_cursor = src_conn.cursor()

                src_cursor.execute("""
                    SELECT id, title, rollout_path, created_at, cwd, model, first_user_message
                    FROM threads
                """)
                threads = src_cursor.fetchall()

                for t in threads:
                    t_id = t['id']
                    session_uid = f"{self.host}:codex:{t_id}"
                    title = t['title'] or (t['first_user_message'][:60] if t['first_user_message'] else "Codex Session")
                    cwd = t['cwd'] or ""
                    model = t['model'] or "codex"
                    started_at = (t['created_at'] / 1000.0) if t['created_at'] else 0.0

                    dest_conn.execute("""
                        INSERT OR REPLACE INTO sessions(uid, host, agent, native_id, title, project, model, started_at, message_count, source_path)
                        VALUES (?, ?, 'codex', ?, ?, ?, ?, ?, 0, ?)
                    """, (
                        session_uid,
                        self.host,
                        t_id,
                        title,
                        cwd,
                        model,
                        started_at,
                        db_path
                    ))

                    dest_conn.execute("DELETE FROM messages WHERE session_uid = ?", (session_uid,))

                    # Parse rollout transcript if path exists
                    rollout = t['rollout_path']
                    batch = []

                    if rollout and os.path.exists(rollout):
                        try:
                            with open(rollout, "r", encoding="utf-8", errors="replace") as fp:
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
                                        content = self.clean_text(content)
                                        if content and content.strip():
                                            batch.append((
                                                session_uid,
                                                self.host,
                                                "codex",
                                                role,
                                                content,
                                                started_at
                                            ))
                                    except Exception:
                                        continue
                        except Exception:
                            pass

                    # If no rollout file but first_user_message exists
                    if not batch and t['first_user_message']:
                        user_msg = self.clean_text(t['first_user_message'])
                        if user_msg.strip():
                            batch.append((
                                session_uid,
                                self.host,
                                "codex",
                                "user",
                                user_msg,
                                started_at
                            ))

                    if batch:
                        dest_conn.executemany("""
                            INSERT INTO messages(session_uid, host, agent, role, content, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, batch)
                        dest_conn.execute("""
                            UPDATE sessions SET message_count = ? WHERE uid = ?
                        """, (len(batch), session_uid))
                        messages_indexed += len(batch)

                dest_conn.commit()
                src_conn.close()
            except Exception:
                continue

        return messages_indexed
