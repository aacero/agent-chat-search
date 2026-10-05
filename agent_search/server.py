import http.server
import json
import os
import sqlite3
import sys
import threading
import urllib.parse
from typing import Dict, Any, Optional

from . import __version__
from .config import get_config
from .schema import init_db, get_db_path
from .search import SearchEngine
from .sync_worker import get_sync_status, run_fleet_sync, is_sync_running
from .mcp_server import handle_request as handle_mcp_request

class AgentSearchHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence access logs except on errors
        pass

    def _check_auth(self) -> bool:
        srv: Any = self.server
        expected_token = getattr(srv, "auth_token", None) or get_config().auth_token
        if not expected_token:
            return True

        # Check Authorization header: Bearer <token>
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token == expected_token:
                return True

        # Check query parameter ?token=...
        parsed = urllib.parse.urlparse(self.path)
        q_params = urllib.parse.parse_qs(parsed.query)
        if q_params.get("token", [""])[0] == expected_token:
            return True

        self._send_json(401, {"error": "Unauthorized: Invalid or missing authentication token"})
        return False

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Agent-Search-Version", __version__)

        origin = self.headers.get("Origin")
        allowed_origins = get_config().allowed_origins
        if origin and allowed_origins:
            if "*" in allowed_origins or origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        origin = self.headers.get("Origin")
        allowed_origins = get_config().allowed_origins
        if origin and allowed_origins:
            if "*" in allowed_origins or origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if not self._check_auth():
            return

        srv: Any = self.server
        conn = srv.get_conn()
        engine = srv.get_engine()

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query_params = urllib.parse.parse_qs(parsed.query)

        if path == "/health" or path == "":
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM sessions")
            sessions_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM messages")
            messages_count = cursor.fetchone()[0]
            sync_st = get_sync_status(conn)
            self._send_json(200, {
                "status": "ok",
                "service": "agent-chat-search",
                "version": __version__,
                "sessions": sessions_count,
                "messages": messages_count,
                "last_synced_at": sync_st["last_synced_at"],
                "age_seconds": sync_st["age_seconds"],
                "sync_in_progress": sync_st["sync_in_progress"]
            })
            return

        if path == "/sync/status":
            sync_st = get_sync_status(conn)
            self._send_json(200, sync_st)
            return

        if path == "/search":
            q_list = query_params.get("q", [])
            if not q_list or not q_list[0].strip():
                self._send_json(400, {"error": "Missing query parameter 'q'"})
                return

            query = q_list[0].strip()
            host = query_params.get("host", [None])[0]
            agent = query_params.get("agent", [None])[0]
            project = query_params.get("project", [None])[0]
            role = query_params.get("role", [None])[0]
            try:
                limit = int(query_params.get("limit", [20])[0])
            except ValueError:
                limit = 20

            # Use markdown/clean tags for API snippet highlight instead of raw terminal ANSI
            results = engine.search(
                query=query,
                host=host,
                agent=agent,
                project=project,
                role=role,
                limit=limit,
                highlight_start="<mark>",
                highlight_end="</mark>"
            )
            self._send_json(200, results)
            return

        if path == "/session":
            s_id_list = query_params.get("id", [])
            if not s_id_list or not s_id_list[0].strip():
                self._send_json(400, {"error": "Missing parameter 'id'"})
                return

            session = engine.get_session(s_id_list[0].strip())
            if not session:
                self._send_json(404, {"error": "Session not found"})
                return

            self._send_json(200, session)
            return

        self._send_json(404, {"error": f"Unknown endpoint: {path}"})

    def do_POST(self):
        if not self._check_auth():
            return

        srv: Any = self.server
        conn = srv.get_conn()
        engine = srv.get_engine()

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query_params = urllib.parse.parse_qs(parsed.query)

        if path == "/sync":
            force = query_params.get("force", ["false"])[0].lower() in ["true", "1", "yes"]
            sync_st = get_sync_status(conn)

            if is_sync_running():
                self._send_json(409, {
                    "status": "already_running",
                    "message": "A fleet sync is currently in progress."
                })
                return

            age = sync_st.get("age_seconds")
            if not force and age is not None and age < 3600:
                self._send_json(200, {
                    "status": "fresh",
                    "age_seconds": age,
                    "last_synced_at": sync_st["last_synced_at"],
                    "message": f"Index is fresh ({age}s old < 3600s). Pass ?force=true to sync anyway."
                })
                return

            def _bg_sync():
                run_fleet_sync(db_path=srv.db_path)

            t = threading.Thread(target=_bg_sync, daemon=True)
            t.start()

            self._send_json(202, {
                "status": "sync_started",
                "message": "Background fleet sync initiated across all live nodes."
            })
            return

        if path == "/mcp":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                req = json.loads(body)
                resp = handle_mcp_request(engine, req)
                self._send_json(200, resp)
            except Exception as e:
                self._send_json(500, {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(e)}
                })
            return

        self._send_json(404, {"error": f"Unknown endpoint: {path}"})

class AgentSearchServer(http.server.ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, db_path=None, auth_token=None):
        super().__init__(server_address, RequestHandlerClass)
        self.db_path = db_path or get_db_path()
        self.auth_token = auth_token or get_config().auth_token
        self._local = threading.local()

    def get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = init_db(self.db_path)
        return self._local.conn

    def get_engine(self) -> SearchEngine:
        if not hasattr(self._local, "engine") or self._local.engine is None:
            self._local.engine = SearchEngine(self.get_conn())
        return self._local.engine

def run_server(host=None, port=None, db_path=None, auth_token=None):
    cfg = get_config()
    bind_host = host or cfg.server_host
    bind_port = port or cfg.server_port
    server = AgentSearchServer(
        (bind_host, bind_port),
        AgentSearchHandler,
        db_path=db_path,
        auth_token=auth_token
    )
    print(f"agent-chat-search server listening on http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_server(port=port)
