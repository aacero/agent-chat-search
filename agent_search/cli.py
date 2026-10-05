import sys
import argparse
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from typing import Optional

from . import __version__
from .config import get_config
from .schema import init_db, get_db_path
from .search import SearchEngine
from .collector import FleetCollector, get_current_hostname
from .mcp_server import run_mcp_server
from .server import run_server

_NOTIFIED_VERSION_MISMATCH = False

def notify_version_mismatch(server_version: Optional[str]):
    global _NOTIFIED_VERSION_MISMATCH
    if not _NOTIFIED_VERSION_MISMATCH and server_version and server_version != __version__:
        _NOTIFIED_VERSION_MISMATCH = True
        sys.stderr.write(
            f"\033[1;33m[Notice] Fleet agent-search version mismatch: local v{__version__} vs server v{server_version}. "
            f"Run './deploy-fleet.sh' to update nodes.\033[0m\n"
        )

def get_headers(auth_token=None):
    headers = {"User-Agent": "agent-search-cli"}
    token = auth_token or get_config().auth_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def format_snippet(snippet: str) -> str:
    return snippet.replace("\n", " ").strip()

def query_remote_server(query: str, host=None, agent=None, project=None, role=None, limit=20, server_url=None, auth_token=None):
    cfg = get_config()
    base_url = (server_url or cfg.server_url).rstrip("/")
    params = {"q": query, "limit": limit}
    if host:
        params["host"] = host
    if agent:
        params["agent"] = agent
    if project:
        params["project"] = project
    if role:
        params["role"] = role

    url = f"{base_url}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=get_headers(auth_token))
    with urllib.request.urlopen(req, timeout=4) as resp:
        notify_version_mismatch(resp.headers.get("X-Agent-Search-Version"))
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
    return None

def get_remote_session(session_uid: str, server_url=None, auth_token=None):
    cfg = get_config()
    base_url = (server_url or cfg.server_url).rstrip("/")
    url = f"{base_url}/session?{urllib.parse.urlencode({'id': session_uid})}"
    req = urllib.request.Request(url, headers=get_headers(auth_token))
    with urllib.request.urlopen(req, timeout=4) as resp:
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
    return None

def cmd_search(args, conn):
    query = " ".join(args.query)
    results = None

    if not args.local_only:
        try:
            results = query_remote_server(
                query=query,
                host=args.host,
                agent=args.agent,
                project=args.project,
                role=args.role,
                limit=args.limit,
                server_url=getattr(args, "server_url", None),
                auth_token=getattr(args, "auth_token", None)
            )
        except Exception:
            results = None

    if results is None:
        local_engine = SearchEngine(conn)
        results = local_engine.search(
            query=query,
            host=args.host,
            agent=args.agent,
            project=args.project,
            role=args.role,
            limit=args.limit
        )

    if args.json_output:
        print(json.dumps(results))
        return

    if not results:
        print(f"No results found for '{query}'.")
        return

    print(f"\033[1;36mFound {len(results)} matches for:\033[0m \033[1m{query}\033[0m\n")
    for i, r in enumerate(results, 1):
        host_badge = f"\033[1;34m[{r['host']}]\033[0m"
        agent_badge = f"\033[1;32m{r['agent'].upper()}\033[0m"
        role_badge = f"\033[1;35m{r['role']}\033[0m"
        date_str = f"\033[0;90m{r['date_str']}\033[0m"
        title_str = f"\033[1m{r['session_title']}\033[0m"

        print(f"{i}. {host_badge} {agent_badge} ({role_badge}) — {title_str}  {date_str}")
        if r.get('session_project'):
            print(f"   \033[0;90mProject:\033[0m {r['session_project']}")
        print(f"   \033[0;90mSession UID:\033[0m {r['session_uid']}")
        print(f"   \033[0;90mSnippet:\033[0m {format_snippet(r['snippet'])}")
        print()

def cmd_show(args, conn):
    session = None
    if not args.local_only:
        try:
            session = get_remote_session(
                args.session_uid,
                server_url=getattr(args, "server_url", None),
                auth_token=getattr(args, "auth_token", None)
            )
        except Exception:
            session = None

    if not session:
        engine = SearchEngine(conn)
        session = engine.get_session(args.session_uid)

    if not session:
        print(f"Session not found: {args.session_uid}")
        sys.exit(1)

    print(f"\033[1;36mSession:\033[0m \033[1m{session['title']}\033[0m")
    print(f"  \033[1mHost:\033[0m     {session['host']}")
    print(f"  \033[1mAgent:\033[0m    {session['agent']}")
    print(f"  \033[1mModel:\033[0m    {session.get('model', '')}")
    print(f"  \033[1mProject:\033[0m  {session.get('project', '')}")
    print(f"  \033[1mStarted:\033[0m  {session['started_str']}")
    print(f"  \033[1mMessages:\033[0m {session['message_count']}")
    print("=" * 70)

    for m in session['messages']:
        role = m['role'].upper()
        if role == "USER":
            role_colored = f"\033[1;32m[{role}]\033[0m"
        elif role == "ASSISTANT":
            role_colored = f"\033[1;34m[{role}]\033[0m"
        else:
            role_colored = f"\033[1;33m[{role}]\033[0m"

        date_str = f"\033[0;90m({m['date_str']})\033[0m"
        print(f"\n{role_colored} {date_str}\n{m['content']}")
        print("-" * 50)

def cmd_sync(args, conn):
    cfg = get_config()
    server_url = (getattr(args, "server_url", None) or cfg.server_url).rstrip("/")
    auth_token = getattr(args, "auth_token", None) or cfg.auth_token

    if getattr(args, "status", False):
        try:
            req = urllib.request.Request(f"{server_url}/sync/status", headers=get_headers(auth_token))
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                print("\033[1;36m=== Fleet Sync Status ===\033[0m")
                print(f"Sync in progress: {data.get('sync_in_progress')}")
                age = data.get('age_seconds')
                age_str = f"{age}s ago ({age // 60}m)" if age is not None else "Never"
                print(f"Overall age:      {age_str}\n")
                print("\033[1mBy Host:\033[0m")
                for h, st in data.get('by_host', {}).items():
                    h_age = f"{st['age_seconds']}s ago" if st.get('age_seconds') is not None else "Never"
                    print(f"  - {h:<16}: status={st['status']:<12} age={h_age:<12} sessions={st['sessions']:<4} messages={st['messages']}")
                return
        except Exception as e:
            print(f"Could not reach server {server_url}: {e}")
            return

    if getattr(args, "fleet", False):
        force_flag = "?force=true" if getattr(args, "force", False) else ""
        try:
            req = urllib.request.Request(
                f"{server_url}/sync{force_flag}",
                data=b"",
                headers=get_headers(auth_token),
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                print(f"Server response: {data.get('status')}")
                print(f"Message: {data.get('message')}")
                return
        except urllib.error.HTTPError as e:
            err_data = json.loads(e.read().decode("utf-8"))
            print(f"Server returned {e.code}: {err_data.get('message')}")
            return
        except Exception as e:
            print(f"Could not trigger fleet sync on {server_url}: {e}")
            return

    collector = FleetCollector()
    print(f"Syncing local chat sources on {collector.current_host}...")
    res = collector.sync_local(conn)
    for k, v in res.items():
        print(f"  - {k}: {v} messages indexed")
    print("\n✓ Local sync complete.")

def cmd_stats(args, conn):
    cfg = get_config()
    server_url = (getattr(args, "server_url", None) or cfg.server_url).rstrip("/")
    auth_token = getattr(args, "auth_token", None) or cfg.auth_token

    # Try fetching server stats first
    try:
        req = urllib.request.Request(f"{server_url}/health", headers=get_headers(auth_token))
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                print(f"\033[1;36m=== Central Fleet Search Server ({server_url}) ===\033[0m")
                print(f"Status:         \033[1;32m{data['status'].upper()}\033[0m")
                print(f"Total sessions: {data['sessions']}")
                print(f"Total messages: {data['messages']}\n")
    except Exception:
        pass

    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM sessions")
    total_sessions = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM messages")
    total_messages = cursor.fetchone()[0]

    print("\033[1;36m=== Local Index Stats ===\033[0m")
    print(f"Database path:  {get_db_path()}")
    db_size = os.path.getsize(get_db_path()) / (1024 * 1024) if os.path.exists(get_db_path()) else 0
    print(f"Database size:  {db_size:.2f} MB")
    print(f"Total sessions: {total_sessions}")
    print(f"Total messages: {total_messages}\n")

    print("\033[1mBy Host & Agent in Local DB:\033[0m")
    cursor.execute("""
        SELECT s.host, s.agent, count(DISTINCT s.uid), count(m.id)
        FROM sessions s
        LEFT JOIN messages m ON m.session_uid = s.uid
        GROUP BY s.host, s.agent
        ORDER BY s.host, s.agent
    """)
    for row in cursor.fetchall():
        print(f"  - {row[0]:<16} | {row[1]:<10} : {row[2]:>4} sessions, {row[3]:>6} messages")

def cmd_purge(args, conn):
    target_host = args.host.strip()
    if not target_host:
        print("Please specify a host to purge.")
        return

    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM sessions WHERE host = ?", (target_host,))
    sess_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM messages WHERE host = ?", (target_host,))
    msg_count = cur.fetchone()[0]

    if sess_count == 0 and msg_count == 0:
        print(f"No records found for host '{target_host}'.")
        return

    if not args.yes:
        try:
            confirm = input(f"Are you sure you want to purge all records for host '{target_host}' ({sess_count} sessions, {msg_count} messages)? [y/N]: ")
            if confirm.lower() not in ["y", "yes"]:
                print("Purge cancelled.")
                return
        except EOFError:
            print("Purge cancelled.")
            return

    conn.execute("DELETE FROM sessions WHERE host = ?", (target_host,))
    conn.execute("DELETE FROM messages WHERE host = ?", (target_host,))
    conn.execute("DELETE FROM sync_meta WHERE host = ?", (target_host,))
    conn.commit()
    conn.execute("VACUUM")
    print(f"✓ Successfully purged {sess_count} sessions and {msg_count} messages for host '{target_host}'.")

def main():
    cfg = get_config()
    parser = argparse.ArgumentParser(
        description="Unified search across Hermes, Google Antigravity, OpenClaw, Codex, and Claude Code chats."
    )
    parser.add_argument("--config", help="Path to config.toml")
    parser.add_argument("--server-url", help="Override central search server URL")
    parser.add_argument("--token", dest="auth_token", help="Bearer authentication token for server")

    subparsers = parser.add_subparsers(dest="command")

    # Search
    search_parser = subparsers.add_parser("search", help="Search historical conversations across fleet")
    search_parser.add_argument("query", nargs="+", help="Search keywords or phrase")
    search_parser.add_argument("--local-only", "--local", action="store_true", help="Only search local database")
    search_parser.add_argument("--host", help="Filter by host")
    search_parser.add_argument("--agent", choices=["hermes", "agy", "openclaw", "codex", "claude"], help="Filter by agent")
    search_parser.add_argument("--project", help="Filter by project substring")
    search_parser.add_argument("--role", choices=["user", "assistant", "tool"], help="Filter by role")
    search_parser.add_argument("--limit", type=int, default=15, help="Max results (default: 15)")
    search_parser.add_argument("--json", dest="json_output", action="store_true", help="Output raw JSON")

    # Show session
    show_parser = subparsers.add_parser("show", help="Show full session transcript")
    show_parser.add_argument("session_uid", help="Session UID or native session ID")
    show_parser.add_argument("--local-only", "--local", action="store_true", help="Only check local database")

    # Sync
    sync_parser = subparsers.add_parser("sync", help="Index local chats or trigger fleet sync")
    sync_parser.add_argument("--local-only", action="store_true", help="Index local chat sources on this host")
    sync_parser.add_argument("--fleet", action="store_true", help="Trigger central fleet sync across all live nodes")
    sync_parser.add_argument("--status", action="store_true", help="Check fleet sync age and status")
    sync_parser.add_argument("--force", action="store_true", help="Force fleet sync even if index is fresh (<1h)")

    # Stats
    subparsers.add_parser("stats", help="Show index statistics")

    # Serve HTTP server
    serve_parser = subparsers.add_parser("serve", help="Start centralized HTTP/MCP search server")
    serve_parser.add_argument("--host", default=None, help=f"Bind address (default: {cfg.server_host})")
    serve_parser.add_argument("--port", type=int, default=None, help=f"Port (default: {cfg.server_port})")

    # MCP server (stdio proxy or direct)
    subparsers.add_parser("mcp", help="Run stdio MCP server for agent integration")

    # Purge decommissioned host
    purge_parser = subparsers.add_parser("purge", help="Purge all indexed records for a decommissioned host")
    purge_parser.add_argument("host", help="Hostname whose sessions and messages should be removed")
    purge_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    if len(sys.argv) > 1 and sys.argv[1] not in ["search", "show", "sync", "stats", "serve", "mcp", "purge", "-h", "--help", "--config", "--server-url", "--token"]:
        sys.argv.insert(1, "search")

    args = parser.parse_args()

    if args.command == "serve":
        run_server(host=args.host, port=args.port, auth_token=args.auth_token)
        return

    if args.command == "mcp":
        run_mcp_server()
        return

    conn = init_db()

    if args.command == "search":
        cmd_search(args, conn)
    elif args.command == "show":
        cmd_show(args, conn)
    elif args.command == "sync":
        cmd_sync(args, conn)
    elif args.command == "stats":
        cmd_stats(args, conn)
    elif args.command == "purge":
        cmd_purge(args, conn)
    else:
        parser.print_help()

if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
