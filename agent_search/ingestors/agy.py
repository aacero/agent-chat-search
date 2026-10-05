import sqlite3
import os
import json
import glob
from pathlib import Path
from datetime import datetime
from .base import BaseIngestor

class AntigravityIngestor(BaseIngestor):
    def __init__(self, host: str, base_dir: str = None):
        super().__init__(host)
        self.base_dir = base_dir or os.path.expanduser("~/.gemini/antigravity-cli")
        self.db_path = os.path.join(self.base_dir, "conversation_summaries.db")

    def _parse_timestamp(self, ts_str: str) -> float:
        if not ts_str:
            return 0.0
        try:
            clean_ts = ts_str.split('+')[0].rstrip('Z')
            if '.' in clean_ts:
                main, frac = clean_ts.split('.')
                frac = (frac + "000000")[:6]
                clean_ts = f"{main}.{frac}"
            dt = datetime.fromisoformat(clean_ts)
            return dt.timestamp()
        except Exception:
            return 0.0

    def _parse_transcript_file(self, transcript_path: str, session_uid: str) -> list:
        batch = []
        if not os.path.exists(transcript_path):
            return batch

        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue

                    raw_type = record.get("type", "")
                    source = record.get("source", "")
                    content = record.get("content", "")
                    ts = self._parse_timestamp(record.get("created_at", ""))

                    role = "user"
                    if raw_type == "USER_INPUT" or source == "USER_EXPLICIT":
                        role = "user"
                    elif raw_type == "PLANNER_RESPONSE" or source == "MODEL":
                        role = "assistant"
                    elif raw_type == "GENERIC":
                        role = "tool"

                    # If content is a list/dict, flatten it
                    if isinstance(content, dict):
                        content = content.get("text", "") or str(content)
                    elif isinstance(content, list):
                        content = "\n".join(
                            item.get("text", "") if isinstance(item, dict) else str(item)
                            for item in content
                        )

                    # Also handle tool_calls if no content on assistant step
                    if not content and record.get("tool_calls"):
                        tool_summaries = []
                        for tc in record["tool_calls"]:
                            name = tc.get("name", "tool")
                            args = tc.get("args", {})
                            summary = args.get("toolSummary") or args.get("toolAction") or ""
                            tool_summaries.append(f"[{name}] {summary}".strip())
                        content = "\n".join(tool_summaries)

                    if content and content.strip():
                        batch.append((
                            session_uid,
                            self.host,
                            "agy",
                            role,
                            self.clean_text(content),
                            ts
                        ))
        except Exception:
            pass

        return batch

    def ingest(self, dest_conn: sqlite3.Connection) -> int:
        if not os.path.exists(self.db_path):
            return 0

        src_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        src_conn.row_factory = sqlite3.Row
        src_cursor = src_conn.cursor()

        messages_indexed = 0

        try:
            src_cursor.execute("""
                SELECT conversation_id, title, preview, step_count, last_modified_time, workspace_uris
                FROM conversation_summaries
            """)
            summaries = src_cursor.fetchall()

            for s in summaries:
                conv_id = s['conversation_id']
                session_uid = f"{self.host}:agy:{conv_id}"

                # Extract project from workspace_uris JSON
                project = ""
                if s['workspace_uris']:
                    try:
                        uris = json.loads(s['workspace_uris'])
                        if uris and isinstance(uris, list):
                            project = uris[0].replace("file://", "")
                    except Exception:
                        project = s['workspace_uris']

                started_at = self._parse_timestamp(s['last_modified_time'])

                dest_conn.execute("""
                    INSERT OR REPLACE INTO sessions(uid, host, agent, native_id, title, project, model, started_at, message_count, source_path)
                    VALUES (?, ?, 'agy', ?, ?, ?, 'gemini', ?, ?, ?)
                """, (
                    session_uid,
                    self.host,
                    conv_id,
                    s['title'] or s['preview'] or "Antigravity Session",
                    project,
                    started_at,
                    s['step_count'] or 0,
                    self.base_dir
                ))

                dest_conn.execute("DELETE FROM messages WHERE session_uid = ?", (session_uid,))

                # 1. First try reading transcript.jsonl
                transcript_file = os.path.join(self.base_dir, "brain", conv_id, ".system_generated", "logs", "transcript.jsonl")
                batch = self._parse_transcript_file(transcript_file, session_uid)

                # 2. Fall back to legacy individual messages/*.json
                if not batch:
                    msg_pattern = os.path.join(self.base_dir, "brain", conv_id, ".system_generated", "messages", "*.json")
                    msg_files = sorted(glob.glob(msg_pattern))

                    for mf in msg_files:
                        if os.path.basename(mf) == "read.json":
                            continue
                        try:
                            with open(mf, "r", encoding="utf-8", errors="replace") as fp:
                                data = json.load(fp)

                            raw_content = data.get("content", "")
                            content = ""
                            if isinstance(raw_content, str):
                                content = raw_content
                            elif isinstance(raw_content, dict):
                                content = raw_content.get("text", "") or str(raw_content)
                            elif isinstance(raw_content, list):
                                content = "\n".join(
                                    item.get("text", "") if isinstance(item, dict) else str(item)
                                    for item in raw_content
                                )

                            if not content.strip():
                                continue

                            sender = data.get("sender", "")
                            role = "user" if "user" in sender.lower() else "assistant"
                            ts = self._parse_timestamp(data.get("timestamp", ""))

                            batch.append((
                                session_uid,
                                self.host,
                                "agy",
                                role,
                                self.clean_text(content),
                                ts
                            ))
                        except Exception:
                            continue

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
        finally:
            src_conn.close()

        return messages_indexed
