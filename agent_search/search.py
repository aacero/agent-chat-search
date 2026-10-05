import sqlite3
import subprocess
import json
import os
import re
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from datetime import datetime
from .config import get_config

def sanitize_fts5_query(query: str) -> str:
    query = query.strip()
    if not query:
        return ""

    # Balance unescaped double quotes if odd count
    if query.count('"') % 2 != 0:
        query = query.replace('"', '')

    raw_tokens = re.findall(r'"[^"]*"|\S+', query)
    tokens = []
    for tok in raw_tokens:
        if tok.startswith('"') and tok.endswith('"'):
            tokens.append(tok)
            continue
        if tok in {"AND", "OR", "NOT"}:
            tokens.append(tok)
            continue
        # Check valid column prefixes e.g. title:foo
        if any(tok.startswith(f"{col}:") for col in ["content", "title", "project"]):
            col, val = tok.split(":", 1)
            clean_val = val.replace('"', '""')
            tokens.append(f'{col}:"{clean_val}"')
            continue
        # If token contains non-alphanumeric chars (except _), wrap in quotes
        if re.search(r"[^\w]", tok):
            clean = tok.replace('"', '""')
            tokens.append(f'"{clean}"')
        else:
            tokens.append(tok)

    return " ".join(tokens)

class SearchEngine:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def search(
        self,
        query: str,
        host: Optional[str] = None,
        agent: Optional[str] = None,
        project: Optional[str] = None,
        role: Optional[str] = None,
        limit: int = 20,
        highlight_start: str = "\033[1;33m",
        highlight_end: str = "\033[0m"
    ) -> List[Dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []

        fts_query = sanitize_fts5_query(clean_query)
        if not fts_query:
            return []

        return self._execute_search(
            fts_query=fts_query,
            original_query=clean_query,
            host=host,
            agent=agent,
            project=project,
            role=role,
            limit=limit,
            highlight_start=highlight_start,
            highlight_end=highlight_end
        )

    def _execute_search(
        self,
        fts_query: str,
        original_query: str,
        host: Optional[str],
        agent: Optional[str],
        project: Optional[str],
        role: Optional[str],
        limit: int,
        highlight_start: str,
        highlight_end: str,
        retry: bool = True
    ) -> List[Dict[str, Any]]:
        conditions = ["messages_fts MATCH ?"]
        params: List[Any] = [fts_query]

        if host:
            conditions.append("messages_fts.host = ?")
            params.append(host)
        if agent:
            conditions.append("messages_fts.agent = ?")
            params.append(agent)
        if project:
            conditions.append("messages_fts.project LIKE ?")
            params.append(f"%{project}%")
        if role:
            conditions.append("messages_fts.role = ?")
            params.append(role)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT
                messages_fts.msg_id,
                messages_fts.session_uid,
                messages_fts.host,
                messages_fts.agent,
                messages_fts.role,
                m.content,
                m.timestamp,
                messages_fts.title,
                messages_fts.project,
                snippet(messages_fts, -1, ?, ?, '...', 24) as snippet,
                bm25(messages_fts) as rank
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.msg_id
            WHERE {where_clause}
            ORDER BY rank
            LIMIT ?
        """
        all_params = [highlight_start, highlight_end] + params + [limit]

        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, all_params)
            rows = cursor.fetchall()

            results = []
            for r in rows:
                ts = r[6]
                ts_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else "Unknown"
                results.append({
                    "id": r[0],
                    "session_uid": r[1],
                    "host": r[2],
                    "agent": r[3],
                    "role": r[4],
                    "content": r[5],
                    "timestamp": ts,
                    "date_str": ts_str,
                    "session_title": r[7],
                    "session_project": r[8],
                    "snippet": r[9],
                    "rank": r[10]
                })
            return results
        except sqlite3.OperationalError as e:
            if retry:
                # Ultimate fallback: quote all words as literal phrases
                words = re.findall(r"\w+", original_query)
                if words:
                    fallback_query = " ".join(f'"{w}"' for w in words)
                    return self._execute_search(
                        fts_query=fallback_query,
                        original_query=original_query,
                        host=host,
                        agent=agent,
                        project=project,
                        role=role,
                        limit=limit,
                        highlight_start=highlight_start,
                        highlight_end=highlight_end,
                        retry=False
                    )
            return []

    def get_session(self, session_uid: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT uid, host, agent, native_id, title, project, model, started_at, message_count, source_path
            FROM sessions
            WHERE uid = ? OR native_id = ?
        """, (session_uid, session_uid))
        s_row = cursor.fetchone()
        if not s_row:
            return None

        uid = s_row[0]
        cursor.execute("""
            SELECT id, role, content, timestamp
            FROM messages
            WHERE session_uid = ?
            ORDER BY id ASC
        """, (uid,))
        m_rows = cursor.fetchall()

        messages = []
        for mr in m_rows:
            ts = mr[3]
            ts_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else "Unknown"
            messages.append({
                "id": mr[0],
                "role": mr[1],
                "content": mr[2],
                "timestamp": ts,
                "date_str": ts_str
            })

        started_ts = s_row[7]
        started_str = datetime.fromtimestamp(started_ts).strftime('%Y-%m-%d %H:%M:%S') if started_ts else "Unknown"

        return {
            "uid": uid,
            "host": s_row[1],
            "agent": s_row[2],
            "native_id": s_row[3],
            "title": s_row[4],
            "project": s_row[5],
            "model": s_row[6],
            "started_at": started_ts,
            "started_str": started_str,
            "message_count": len(messages),
            "source_path": s_row[9],
            "messages": messages
        }

class FederatedSearchEngine:
    def __init__(
        self,
        local_engine: SearchEngine,
        hosts: Optional[List[str]] = None,
        current_host: Optional[str] = None,
        ssh_key: Optional[str] = None,
        ssh_user: Optional[str] = None
    ):
        cfg = get_config()
        self.local_engine = local_engine
        self.hosts = hosts if hosts is not None else cfg.hosts
        self.current_host = current_host or "localhost"
        self.ssh_key = os.path.expanduser(ssh_key or cfg.ssh_key)
        self.ssh_user = ssh_user or cfg.ssh_user

    def _query_remote_host(
        self,
        host: str,
        query: str,
        agent: Optional[str],
        project: Optional[str],
        role: Optional[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        remote_args = ["~/.local/bin/agent-search", "search", "--local-only", "--json"]
        if agent:
            remote_args.extend(["--agent", agent])
        if project:
            remote_args.extend(["--project", project])
        if role:
            remote_args.extend(["--role", role])
        remote_args.extend(["--limit", str(limit)])
        remote_args.append(query)

        # Securely escape all arguments for SSH remote shell invocation
        escaped_cmd = " ".join(shlex.quote(a) for a in remote_args)

        cmd = [
            "ssh", "-i", self.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{self.ssh_user}@{host}",
            escaped_cmd
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout)
        except Exception:
            pass
        return []

    def search_fleet(
        self,
        query: str,
        host: Optional[str] = None,
        agent: Optional[str] = None,
        project: Optional[str] = None,
        role: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        target_hosts = [host] if host else self.hosts
        if not target_hosts:
            return self.local_engine.search(query, host, agent, project, role, limit)

        all_results = []
        with ThreadPoolExecutor(max_workers=max(1, len(target_hosts))) as executor:
            futures = {}
            for h in target_hosts:
                if h == self.current_host:
                    futures[executor.submit(self.local_engine.search, query, h, agent, project, role, limit)] = h
                else:
                    futures[executor.submit(self._query_remote_host, h, query, agent, project, role, limit)] = h

            for f in as_completed(futures):
                try:
                    res = f.result()
                    if res:
                        all_results.extend(res)
                except Exception:
                    pass

        # Sort merged results by BM25 rank (lower is better in SQLite bm25)
        all_results.sort(key=lambda x: x.get("rank", 0.0))
        return all_results[:limit]
