import sqlite3
import os
from pathlib import Path
from .base import BaseIngestor

class HermesIngestor(BaseIngestor):
    def __init__(self, host: str, db_path: str = None):
        super().__init__(host)
        self.db_path = db_path or os.path.expanduser("~/.hermes/state.db")

    def ingest(self, dest_conn: sqlite3.Connection) -> int:
        if not os.path.exists(self.db_path):
            return 0

        src_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        src_conn.row_factory = sqlite3.Row
        src_cursor = src_conn.cursor()

        messages_indexed = 0

        try:
            # Query sessions
            src_cursor.execute("""
                SELECT id, title, model, started_at, cwd, git_repo_root, message_count
                FROM sessions
            """)
            sessions = src_cursor.fetchall()

            for s in sessions:
                session_uid = f"{self.host}:hermes:{s['id']}"
                project = s['git_repo_root'] or s['cwd'] or ""

                dest_conn.execute("""
                    INSERT OR REPLACE INTO sessions(uid, host, agent, native_id, title, project, model, started_at, message_count, source_path)
                    VALUES (?, ?, 'hermes', ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_uid,
                    self.host,
                    s['id'],
                    s['title'] or "Untitled Session",
                    project,
                    s['model'] or "",
                    s['started_at'],
                    s['message_count'] or 0,
                    self.db_path
                ))

                # Clear old messages for clean re-index of this session
                dest_conn.execute("DELETE FROM messages WHERE session_uid = ?", (session_uid,))

                # Query messages for this session
                msg_cur = src_conn.cursor()
                msg_cur.execute("""
                    SELECT role, content, timestamp
                    FROM messages
                    WHERE session_id = ? AND content IS NOT NULL AND length(content) > 0
                    ORDER BY id ASC
                """, (s['id'],))

                batch = []
                for m in msg_cur.fetchall():
                    clean_content = self.clean_text(m['content'])
                    if clean_content.strip():
                        batch.append((
                            session_uid,
                            self.host,
                            "hermes",
                            m['role'],
                            clean_content,
                            m['timestamp']
                        ))

                if batch:
                    dest_conn.executemany("""
                        INSERT INTO messages(session_uid, host, agent, role, content, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, batch)
                    messages_indexed += len(batch)

            dest_conn.commit()
        finally:
            src_conn.close()

        return messages_indexed
