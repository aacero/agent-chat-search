# agent-chat-search

**Unified, zero-token full-text search across all your AI coding agents and machines.**

Search conversational history, debugging traces, and code diffs across **Hermes Agent**, **Google Antigravity (`agy`)**, **OpenClaw**, **OpenAI Codex**, and **Anthropic Claude Code** spanning your local workstation or an entire Tailscale mesh.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/aacero/agent-chat-search/actions/workflows/test.yml/badge.svg)](https://github.com/aacero/agent-chat-search/actions/workflows/test.yml)
[![Architecture: Local--First](https://img.shields.io/badge/architecture-local--first-green.svg)]()
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)]()

---

## The Problem: The Agent Silo

Developers increasingly work with multiple autonomous coding agents across multiple machines — laptops, cloud VMs, and desktop workstations:
* You fix an obscure kernel or build issue on your workstation using **Google Antigravity**.
* Two days later, **Claude Code** or **Hermes Agent** running on your laptop encounters the exact same failure.
* **The agent has no memory of the fix**, because every tool stores its session history in an isolated, incompatible silo.

### Why not RAG or Vector Databases?
* **Token Cost & GPU Waste:** Chunking and embedding hundreds of megabytes of raw terminal logs, tool outputs, and compiler diffs burns millions of LLM tokens and saturates GPUs.
* **Lexical Superiority:** Exact error codes (`evdi#557`, `0000:00:14.0`), compiler flags (`-ctk q4_0`), package versions, and paths are retrieved far more accurately with **BM25 full-text search** than fuzzy semantic vector embeddings.
* **Obsidian/Git Bloat:** Funneling raw agent transcripts into Obsidian or Git repositories explodes repo size, merges conflict, and chokes mobile sync.

---

## The Solution: `agent-chat-search`

`agent-chat-search` decouples agent history into a lightweight, local-first search engine:

1. **Multi-Runtime Ingestors:** Automatically discovers and parses transcripts from:
   * **Hermes Agent:** Relational SQLite (`~/.hermes/state.db`)
   * **Google Antigravity (`agy`):** Metadata SQLite (`conversation_summaries.db`) + JSON message trees (`brain/<uuid>`)
   * **OpenClaw:** Event-sourced SQLite (`openclaw-agent.sqlite`)
   * **OpenAI Codex CLI:** Thread DB (`state_*.sqlite`) + JSONL rollout transcripts
   * **Anthropic Claude Code:** JSON sessions & project transcripts
2. **Zero Dependencies:** Pure Python standard library (`sqlite3`, `http.server`, `urllib`, `json`). Zero external packages required.
3. **Sub-Millisecond Performance:** Backed by SQLite FTS5 with BM25 ranking and context snippet highlighting.
4. **Tailscale Native:** Operates as a fast, private HTTP/MCP service over your Tailnet, transferring only matching snippets (~2–5 KB per query).
5. **Dual Interface:** Serves humans at the terminal (`agent-search`) and agents via Model Context Protocol (`mcp_servers`).

---

## Fleet Baseline Benchmark

Across 5 nodes in an active multi-agent mesh, `agent-chat-search` indexed **427 sessions** and **30,725 messages** into an optimized **~43 MB** database:

| Machine | Hermes | Antigravity | OpenClaw | Codex | Claude Code | Indexed Messages | DB Size |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **workstation-main** | 6,460 | 488 | 684 | 0 | 0 | 7,620 | 21.2 MB |
| **server-node** | 13,766 | 16 | 1,438 | 0 | 0 | 15,220 | 48.3 MB |
| **dev-laptop** | 3,491 | 1 | 1,021 | 0 | 0 | 4,513 | 10.5 MB |
| **mini-pc-1** | 631 | 0 | 1,780 | 0 | 0 | 2,411 | 5.3 MB |
| **mini-pc-2** | 784 | 0 | 177 | 0 | 0 | 961 | 5.1 MB |
| **Consolidated** | **25,132** | **505** | **5,100** | — | — | **30,725** | **~43.0 MB** |

*Search query latency: **< 15 milliseconds** over Tailscale.*

---

## Installation

### One-Line Install
```bash
git clone https://github.com/aacero/agent-chat-search.git ~/Projects/agent-chat-search
cd ~/Projects/agent-chat-search
./install.sh
```

This installs `agent-search` directly to `~/.local/bin/agent-search` (ensure `~/.local/bin` is in your `$PATH`).

---

## Quick Start

### 1. Index Local Machine Chats
```bash
agent-search sync
```

### 2. Search Past Conversations
```bash
# General search with BM25 ranking and highlighted snippets
agent-search "DisplayLink evdi freeze"

# Filter by agent runtime
agent-search --agent hermes "USB watchdog migration"
agent-search --agent agy "display brightness config"
agent-search --agent openclaw "onboarding model api key"
agent-search --agent codex "refactor schema"

# Filter by speaker role (e.g. only prompts you typed)
agent-search --role user "hard freeze"

# Filter by host
agent-search --host server-node "backup"

# Limit to local database only
agent-search --local "Tailscale"
```

### 3. Read Full Session Transcript
```bash
agent-search show "server-node:hermes:20260826_134805_88f136"
```

---

## Multi-Machine Fleet Setup (Tailscale)

In a multi-machine setup, designate one always-on node (e.g. a home server or workstation) as the central index server:

### On the Server Node (e.g. `server-node` / `100.64.0.1`):
Run as a systemd user service:

```ini
# ~/.config/systemd/user/agent-chat-search.service
[Unit]
Description=Agent Chat Search Service
After=network.target

[Service]
Type=simple
Environment=PYTHONPATH=%h/.local/share/agent-chat-search-app
ExecStart=%h/.local/bin/agent-search serve --host 127.0.0.1 --port 8890
Restart=always

[Install]
WantedBy=default.target
```

Enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable --now agent-chat-search
```

### On Client Nodes (Laptops, Desktops):
Point the CLI to your server node (and optional bearer token):
```bash
export AGENT_SEARCH_URL="http://100.64.0.1:8890"
# export AGENT_SEARCH_TOKEN="your-secret-token"
```

Queries will automatically hit the central server over Tailscale in <20ms, falling back to the local SQLite database if the server is unreachable.

---

## Managing Fleet Nodes (Adding & Deleting)

### 1. Adding a New Node to the Fleet

To include a new machine (e.g. `gpu-workstation`) in the federated fleet search:

1. **Install on the New Node:**
   ```bash
   git clone <repo-url> ~/src/agent-chat-search
   cd ~/src/agent-chat-search
   ./install.sh
   ```
2. **Configure Client on the New Node:**
   Create `~/.config/agent-chat-search/config.toml` on the new machine:
   ```toml
   [server]
   url = "http://<central-server-tailscale-ip>:8890"
   # auth_token = "your-optional-token"
   ```
   *(Or set `export AGENT_SEARCH_URL="http://<central-server-tailscale-ip>:8890"` in `~/.bashrc`)*.
3. **Index Local Chats on the New Node:**
   ```bash
   agent-search sync --local-only
   ```
4. **Register in Fleet Configuration:**
   On your admin workstation or central server, add the new hostname to `hosts` in `~/.config/agent-chat-search/config.toml`:
   ```toml
   [fleet]
   hosts = [
       "server-node",
       "dev-laptop",
       "gpu-workstation"
   ]
   ```
5. **Propagate and Sync:**
   Deploy updated files and trigger a fleet sync:
   ```bash
   ./deploy-fleet.sh
   agent-search sync --fleet --force
   ```

---

### 2. Decommissioning / Deleting a Node from the Fleet

To remove an old node from the fleet index:

1. **Remove Host from Fleet Config:**
   In `~/.config/agent-chat-search/config.toml`, remove the hostname from `hosts = [...]`.
   Run `./deploy-fleet.sh` to propagate the updated host list.
2. **Purge Historical Data from Database:**
   To permanently purge the decommissioned host's sessions, messages, and full-text index records from the SQLite database:
   ```bash
   agent-search purge old-node
   ```
   *(Use `-y` or `--yes` to skip the confirmation prompt).*
   This deletes the host's records from `sessions`, cascades the deletion to `messages` and `messages_fts`, and reclaims disk space with `VACUUM`.

---

## Agent Integration (MCP)

`agent-chat-search` acts as a Model Context Protocol (MCP) server so external agents can recall past conversations.

### For Hermes Agent
Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  agent_search:
    command: /home/user/.local/bin/agent-search
    args:
      - mcp
```

### Tools Exposed to Agents:
* **`fleet_chat_search`**: Searches historical conversations across all agents and machines with ranking and code snippets.
* **`fleet_chat_get_session`**: Retrieves the full message transcript of any past session by its unique ID.

---

## Architecture Overview

```
[Workstation]          [Laptop]              [Cloud VM]
  Hermes / agy / claw    Hermes / Codex        Hermes / Claude Code
       │                      │                      │
  (Local Index)          (Local Index)          (Local Index)
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              │ Tailscale
                              ▼
            ┌───────────────────────────────────┐
            │   Central Search Daemon (:8890)   │
            │      SQLite FTS5 + BM25           │
            └─────────────────┬─────────────────┘
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
       CLI (`agent-search`)           MCP Server (:mcp)
     (Fast human terminal)       (Cross-agent episodic memory)
```

---

## License

MIT License © 2026 Tony Acero
