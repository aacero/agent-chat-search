import os
import socket
import subprocess
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import get_config
from .ingestors.hermes import HermesIngestor
from .ingestors.agy import AntigravityIngestor
from .ingestors.openclaw import OpenClawIngestor
from .ingestors.codex import CodexIngestor
from .ingestors.claude import ClaudeCodeIngestor

def get_current_hostname() -> str:
    try:
        with open("/etc/hostname", "r") as f:
            h = f.read().strip()
            if h:
                return h
    except Exception:
        pass
    return socket.gethostname().split('.')[0]

class FleetCollector:
    def __init__(self, cache_dir: Optional[str] = None, ssh_key: Optional[str] = None, ssh_user: Optional[str] = None):
        cfg = get_config()
        self.current_host = get_current_hostname()
        self.cache_dir = Path(cache_dir or os.path.expanduser("~/.cache/agent-chat-search"))
        self.ssh_key = os.path.expanduser(ssh_key or cfg.ssh_key)
        self.ssh_user = ssh_user or cfg.ssh_user
        self.sources = cfg.sources

    def sync_local(self, dest_conn: sqlite3.Connection) -> Dict[str, int]:
        results = {}

        # Hermes
        if self.sources.get("hermes", True):
            try:
                h = HermesIngestor(self.current_host)
                results["hermes"] = h.ingest(dest_conn)
            except Exception as e:
                results["hermes_error"] = str(e)

        # Antigravity
        if self.sources.get("antigravity", True):
            try:
                a = AntigravityIngestor(self.current_host)
                results["agy"] = a.ingest(dest_conn)
            except Exception as e:
                results["agy_error"] = str(e)

        # OpenClaw
        if self.sources.get("openclaw", True):
            try:
                o = OpenClawIngestor(self.current_host)
                results["openclaw"] = o.ingest(dest_conn)
            except Exception as e:
                results["openclaw_error"] = str(e)

        # Codex
        if self.sources.get("codex", True):
            try:
                c = CodexIngestor(self.current_host)
                results["codex"] = c.ingest(dest_conn)
            except Exception as e:
                results["codex_error"] = str(e)

        # Claude Code
        if self.sources.get("claude", True):
            try:
                cl = ClaudeCodeIngestor(self.current_host)
                results["claude"] = cl.ingest(dest_conn)
            except Exception as e:
                results["claude_error"] = str(e)

        return results

    def sync_remote(self, dest_conn: sqlite3.Connection, remote_host: str) -> Dict[str, int]:
        if remote_host == self.current_host:
            return self.sync_local(dest_conn)

        results = {}
        host_cache = self.cache_dir / "remote" / remote_host
        host_cache.mkdir(parents=True, exist_ok=True)

        ssh_opts = f"ssh -i {self.ssh_key} -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"

        # 1. Sync remote Hermes state.db
        if self.sources.get("hermes", True):
            hermes_target = host_cache / "hermes"
            hermes_target.mkdir(parents=True, exist_ok=True)
            cmd_hermes = [
                "rsync", "-e", ssh_opts, "-aqz", "--delete",
                f"{self.ssh_user}@{remote_host}:~/.hermes/state.db*",
                str(hermes_target) + "/"
            ]
            try:
                res = subprocess.run(cmd_hermes, capture_output=True, timeout=180)
                if res.returncode == 0:
                    h_db = str(hermes_target / "state.db")
                    if os.path.exists(h_db):
                        h_ingest = HermesIngestor(remote_host, db_path=h_db)
                        results["hermes"] = h_ingest.ingest(dest_conn)
            except Exception as e:
                results["hermes_error"] = str(e)

        # 2. Sync remote Antigravity
        if self.sources.get("antigravity", True):
            agy_target = host_cache / "gemini" / "antigravity-cli"
            agy_target.mkdir(parents=True, exist_ok=True)
            cmd_agy = [
                "rsync", "-e", ssh_opts, "-aqz", "--delete",
                "--include=conversation_summaries.db*",
                "--include=brain/***",
                "--exclude=*",
                f"{self.ssh_user}@{remote_host}:~/.gemini/antigravity-cli/",
                str(agy_target) + "/"
            ]
            try:
                res = subprocess.run(cmd_agy, capture_output=True, timeout=180)
                if res.returncode == 0:
                    a_ingest = AntigravityIngestor(remote_host, base_dir=str(agy_target))
                    results["agy"] = a_ingest.ingest(dest_conn)
            except Exception as e:
                results["agy_error"] = str(e)

        # 3. Sync remote OpenClaw agents sqlite
        if self.sources.get("openclaw", True):
            oc_target = host_cache / "openclaw" / "agents"
            oc_target.mkdir(parents=True, exist_ok=True)
            cmd_oc = [
                "rsync", "-e", ssh_opts, "-aqz", "--delete",
                "--include=*/",
                "--include=*/agent/",
                "--include=*/agent/openclaw-agent.sqlite*",
                "--exclude=*",
                f"{self.ssh_user}@{remote_host}:~/.openclaw/agents/",
                str(oc_target) + "/"
            ]
            try:
                res = subprocess.run(cmd_oc, capture_output=True, timeout=180)
                if res.returncode == 0:
                    o_ingest = OpenClawIngestor(remote_host, base_dir=str(oc_target))
                    results["openclaw"] = o_ingest.ingest(dest_conn)
            except Exception as e:
                results["openclaw_error"] = str(e)

        return results

    def sync_fleet(self, dest_conn: sqlite3.Connection, hosts: Optional[List[str]] = None) -> Dict[str, Dict[str, int]]:
        target_hosts = hosts if hosts is not None else get_config().hosts
        fleet_results = {}
        for h in target_hosts:
            if h == self.current_host:
                fleet_results[h] = self.sync_local(dest_conn)
            else:
                fleet_results[h] = self.sync_remote(dest_conn, h)
        return fleet_results
