import sqlite3
import os
import json
import glob
from pathlib import Path
from .base import BaseIngestor

class OpenClawIngestor(BaseIngestor):
    def __init__(self, host: str, base_dir: str = None):
        super().__init__(host)
        self.base_dir = base_dir or os.path.expanduser("~/.openclaw/agents")

    def ingest(self, dest_conn: sqlite3.Connection) -> int:
        if not os.path.exists(self.base_dir):
            return 0

        pattern = os.path.join(self.base_dir, "*", "agent", "openclaw-agent.sqlite")
        db_paths = glob.glob(pattern)

        messages_indexed = 0

        for db_path in db_paths:
            agent_name = Path(db_path).parent.parent.name
            try:
                src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                src_cursor = src_conn.cursor()

                # Get all message events
                src_cursor.execute("""
                    SELECT session_id, event_json, created_at
                    FROM transcript_events
                    WHERE event_json IS NOT NULL
                    ORDER BY seq ASC
                """)

                sessions_map = {}
                messages_by_session = {}

                for row in src_cursor.fetchall():
                    sess_id = row[0]
                    raw_event = row[1]
                    created_at = row[2] / 1000.0 if row[2] else 0.0

                    try:
                        event = json.loads(raw_event)
                    except Exception:
                        continue

                    etype = event.get("type")
                    if etype == "session":
                        cwd = event.get("cwd", "")
                        sessions_map[sess_id] = {
                            "project": cwd,
                            "started_at": created_at
                        }
                    elif etype == "message":
                        msg = event.get("message", {})
                        role = msg.get("role", "user")
                        raw_content = msg.get("content", "")

                        content = ""
                        if isinstance(raw_content, str):
                            content = raw_content
                        elif isinstance(raw_content, list):
                            parts = []
                            for p in raw_content:
                                if isinstance(p, dict):
                                    parts.append(p.get("text", "") or "")
                                else:
                                    parts.append(str(p))
                            content = "\n".join(parts)

                        content = self.clean_text(content)
                        if not content.strip():
                            continue

                        if sess_id not in messages_by_session:
                            messages_by_session[sess_id] = []

                        messages_by_session[sess_id].append((
                            role,
                            content,
                            created_at
                        ))

                # Insert sessions and messages
                all_sessions = set(list(sessions_map.keys()) + list(messages_by_session.keys()))
                for s_id in all_sessions:
                    s_info = sessions_map.get(s_id, {})
                    session_uid = f"{self.host}:openclaw:{agent_name}:{s_id}"
                    project = s_info.get("project", "")
                    started_at = s_info.get("started_at", 0.0)
                    msg_list = messages_by_session.get(s_id, [])

                    # Generate title from first user message if available
                    title = f"OpenClaw ({agent_name}) Session"
                    for r, c, _ in msg_list:
                        if r == "user":
                            title = c.split("\n")[0][:60]
                            break

                    dest_conn.execute("""
                        INSERT OR REPLACE INTO sessions(uid, host, agent, native_id, title, project, model, started_at, message_count, source_path)
                        VALUES (?, ?, 'openclaw', ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        session_uid,
                        self.host,
                        s_id,
                        title,
                        project,
                        agent_name,
                        started_at,
                        len(msg_list),
                        db_path
                    ))

                    dest_conn.execute("DELETE FROM messages WHERE session_uid = ?", (session_uid,))

                    batch = [
                        (session_uid, self.host, "openclaw", r, c, ts)
                        for r, c, ts in msg_list
                    ]
                    if batch:
                        dest_conn.executemany("""
                            INSERT INTO messages(session_uid, host, agent, role, content, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, batch)
                        messages_indexed += len(batch)

                dest_conn.commit()
                src_conn.close()
            except Exception:
                continue

        return messages_indexed
