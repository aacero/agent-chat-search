import os
import getpass
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

CONFIG_DIR = Path.home() / ".config" / "agent-chat-search"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.toml"

class Config:
    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        self.raw = config_dict or {}

        # Database
        db_cfg = self.raw.get("database", {})
        default_db = str(Path.home() / ".local" / "share" / "agent-chat-search" / "fleet_chats.db")
        self.db_path = os.path.expanduser(
            os.environ.get("AGENT_SEARCH_DB") or db_cfg.get("path") or default_db
        )

        # Server
        srv_cfg = self.raw.get("server", {})
        self.server_host = os.environ.get("AGENT_SEARCH_HOST") or srv_cfg.get("host") or "127.0.0.1"
        self.server_port = int(os.environ.get("AGENT_SEARCH_PORT") or srv_cfg.get("port") or 8890)
        self.server_url = (
            os.environ.get("AGENT_SEARCH_URL") or srv_cfg.get("url") or f"http://127.0.0.1:{self.server_port}"
        ).rstrip("/")
        self.auth_token = os.environ.get("AGENT_SEARCH_TOKEN") or srv_cfg.get("auth_token") or ""

        # Fleet
        fleet_cfg = self.raw.get("fleet", {})
        self.ssh_user = (
            os.environ.get("AGENT_SEARCH_SSH_USER") or fleet_cfg.get("ssh_user") or getpass.getuser()
        )
        self.ssh_key = os.path.expanduser(
            os.environ.get("AGENT_SEARCH_SSH_KEY") or fleet_cfg.get("ssh_key") or "~/.ssh/id_ed25519"
        )
        env_hosts = os.environ.get("AGENT_SEARCH_HOSTS")
        if env_hosts:
            self.hosts = [h.strip() for h in env_hosts.split(",") if h.strip()]
        else:
            self.hosts = fleet_cfg.get("hosts") or []

        # Sources
        src_cfg = self.raw.get("sources", {})
        self.sources = {
            "hermes": src_cfg.get("hermes", True),
            "antigravity": src_cfg.get("antigravity", True),
            "openclaw": src_cfg.get("openclaw", True),
            "codex": src_cfg.get("codex", True),
            "claude": src_cfg.get("claude", True),
        }

        # Security
        sec_cfg = self.raw.get("security", {})
        self.redact_secrets = sec_cfg.get("redact_secrets", True)
        self.allowed_origins = sec_cfg.get("allowed_origins") or []

def load_config(config_path: Optional[str] = None) -> Config:
    path_to_load = config_path or os.environ.get("AGENT_SEARCH_CONFIG")
    if not path_to_load:
        path_to_load = str(DEFAULT_CONFIG_PATH)

    expanded_path = os.path.expanduser(path_to_load)
    if os.path.exists(expanded_path) and tomllib is not None:
        try:
            with open(expanded_path, "rb") as f:
                data = tomllib.load(f)
                return Config(data)
        except Exception:
            pass

    return Config()

_GLOBAL_CONFIG: Optional[Config] = None

def get_config() -> Config:
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = load_config()
    return _GLOBAL_CONFIG
