# Contributing to agent-chat-search

Thank you for considering contributing to `agent-chat-search`!

The core philosophy of `agent-chat-search` is:
1. **Zero External Dependencies:** Use pure Python standard library modules (`sqlite3`, `http.server`, `urllib`, `json`).
2. **Sub-millisecond Performance:** Fast local SQLite FTS5 search with BM25 ranking.
3. **Local-First & Private:** Transcripts stay on your machines or private mesh. Sensitive tokens and keys are automatically sanitized before indexing.

---

## Development Setup

Clone the repository and run the test suite:

```bash
git clone https://github.com/aacero/agent-chat-search.git
cd agent-chat-search

# Run unit tests
python3 -m unittest discover -s tests
```

---

## Adding a New Agent Ingestor

Support for new coding agents (e.g. Cursor, Aider, Windsurf, Devika, Goose) is warmly welcomed!

To add support for a new agent:

1. **Create an ingestor module:**
   Add `agent_search/ingestors/<agent_name>.py` subclassing `BaseIngestor` from `agent_search.ingestors.base`:

   ```python
   from agent_search.ingestors.base import BaseIngestor, IngestSession, IngestMessage

   class MyAgentIngestor(BaseIngestor):
       def __init__(self, host: str, base_dir: str = None):
           super().__init__(host)
           # Identify base storage directory for the agent

       def discover_sessions(self) -> list[IngestSession]:
           # Locate session metadata, transcript files, or database rows
           ...

       def parse_messages(self, session: IngestSession) -> list[IngestMessage]:
           # Parse messages, extracting role (user/assistant/system), content, and timestamp
           ...
   ```

2. **Register the ingestor in `agent_search/collector.py`:**
   Add your ingestor to `get_available_ingestors()`.

3. **Add unit tests:**
   Add a test case in `tests/test_agent_search.py` verifying transcript parsing using mock files in a temporary directory.

---

## Submitting Pull Requests

1. Create a descriptive branch: `git checkout -b feature/support-aider`
2. Ensure all tests pass: `python3 -m unittest discover -s tests`
3. Commit with a clear message and submit a PR!
