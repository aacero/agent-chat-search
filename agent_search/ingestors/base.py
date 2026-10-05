import abc
import sqlite3
from typing import List, Dict, Any, Optional
from ..config import get_config
from ..redact import redact_sensitive_text

class BaseIngestor(abc.ABC):
    def __init__(self, host: str):
        self.host = host
        self.config = get_config()

    def clean_text(self, text: str) -> str:
        if not text:
            return ""
        if self.config.redact_secrets:
            return redact_sensitive_text(text)
        return text

    @abc.abstractmethod
    def ingest(self, dest_conn: sqlite3.Connection) -> int:
        """
        Ingests sessions and messages into dest_conn.
        Returns the number of messages indexed.
        """
        pass
