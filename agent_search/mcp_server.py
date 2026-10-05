import sys
import json
import sqlite3
from typing import Dict, Any

from .schema import init_db
from .search import SearchEngine
from .config import get_config

def handle_request(engine: SearchEngine, req: Dict[str, Any]) -> Dict[str, Any]:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "agent-chat-search",
                    "version": "1.0.0"
                }
            }
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "fleet_chat_search",
                        "description": (
                            "Search conversational transcripts across AI coding agents (Hermes, Google Antigravity, "
                            "OpenClaw, Codex, Claude Code) across configured fleet nodes. "
                            "Returns ranked snippets, session titles, agent types, and timestamps."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Full-text search query (e.g. 'DisplayLink freeze', 'brightness control')"
                                },
                                "host": {
                                    "type": "string",
                                    "description": "Optional host filter"
                                },
                                "agent": {
                                    "type": "string",
                                    "enum": ["hermes", "agy", "openclaw", "codex", "claude"],
                                    "description": "Optional agent filter: hermes, agy, openclaw, codex, claude"
                                },
                                "project": {
                                    "type": "string",
                                    "description": "Optional project substring filter"
                                },
                                "role": {
                                    "type": "string",
                                    "enum": ["user", "assistant"],
                                    "description": "Optional role filter: user or assistant"
                                },
                                "limit": {
                                    "type": "integer",
                                    "default": 10,
                                    "description": "Max results to return (default 10, max 50)"
                                }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "fleet_chat_get_session",
                        "description": "Retrieve the full message transcript and metadata for a specific session UID.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "session_uid": {
                                    "type": "string",
                                    "description": "Session UID (e.g. '<host>:hermes:<session_id>' or '<host>:agy:<uuid>')"
                                }
                            },
                            "required": ["session_uid"]
                        }
                    }
                ]
            }
        }

    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "fleet_chat_search":
            query = args.get("query", "")
            host = args.get("host")
            agent = args.get("agent")
            project = args.get("project")
            role = args.get("role")
            limit = min(int(args.get("limit", 10)), 50)

            results = engine.search(
                query,
                host=host,
                agent=agent,
                project=project,
                role=role,
                limit=limit,
                highlight_start="**",
                highlight_end="**"
            )

            lines = [f"Found {len(results)} matches for: **{query}**\n"]
            for i, r in enumerate(results, 1):
                snippet = r['snippet'].replace('\033[1;33m', '**').replace('\033[0m', '**')
                lines.append(
                    f"{i}. **[{r['host']}] {r['agent'].upper()}** — _{r['session_title']}_\n"
                    f"   *Session ID:* `{r['session_uid']}` | *Date:* {r['date_str']} | *Role:* {r['role']}\n"
                    f"   *Snippet:* {snippet}\n"
                )
            output_text = "\n".join(lines)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": output_text}]
                }
            }

        elif tool_name == "fleet_chat_get_session":
            s_uid = args.get("session_uid", "")
            session = engine.get_session(s_uid)
            if not session:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Session '{s_uid}' not found."}]
                    }
                }

            header = (
                f"# Session: {session['title']}\n"
                f"- **Host:** {session['host']}\n"
                f"- **Agent:** {session['agent']}\n"
                f"- **Model:** {session['model']}\n"
                f"- **Project:** {session['project']}\n"
                f"- **Started:** {session['started_str']}\n"
                f"- **Messages:** {session['message_count']}\n\n---\n"
            )
            msg_lines = []
            for m in session['messages']:
                msg_lines.append(f"### [{m['role'].upper()}] ({m['date_str']})\n\n{m['content']}\n")
            full_text = header + "\n".join(msg_lines)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": full_text}]
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {}
    }

def run_mcp_server():
    conn = init_db()
    engine = SearchEngine(conn)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(engine, req)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)}
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
