import unittest
import sqlite3
import tempfile
import json
import os
from pathlib import Path

from agent_search.config import Config, load_config
from agent_search.redact import redact_sensitive_text
from agent_search.search import sanitize_fts5_query, SearchEngine
from agent_search.schema import SCHEMA_SQL, init_db
from agent_search.ingestors.agy import AntigravityIngestor
from agent_search.ingestors.claude import ClaudeCodeIngestor

class TestConfig(unittest.TestCase):
    def test_default_config(self):
        cfg = Config({})
        self.assertEqual(cfg.server_host, "127.0.0.1")
        self.assertEqual(cfg.server_port, 8890)
        self.assertEqual(cfg.auth_token, "")
        self.assertTrue(cfg.redact_secrets)
        self.assertEqual(cfg.sources["hermes"], True)

    def test_env_override(self):
        os.environ["AGENT_SEARCH_TOKEN"] = "test-token-123"
        os.environ["AGENT_SEARCH_HOST"] = "0.0.0.0"
        cfg = Config({})
        self.assertEqual(cfg.auth_token, "test-token-123")
        self.assertEqual(cfg.server_host, "0.0.0.0")
        del os.environ["AGENT_SEARCH_TOKEN"]
        del os.environ["AGENT_SEARCH_HOST"]

class TestRedact(unittest.TestCase):
    def test_api_keys_redacted(self):
        sample = "My key is sk-12345678901234567890123456789012 and ant key sk-ant-12345678901234567890123456789012"
        redacted = redact_sensitive_text(sample)
        self.assertNotIn("sk-1234", redacted)
        self.assertIn("[REDACTED_OPENAI_KEY]", redacted)
        self.assertIn("[REDACTED_ANTHROPIC_KEY]", redacted)

    def test_github_token_redacted(self):
        sample = "Token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        redacted = redact_sensitive_text(sample)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", redacted)

class TestFTS5Sanitizer(unittest.TestCase):
    def test_sanitization(self):
        cases = [
            ("0000:00:14.0", '"0000:00:14.0"'),
            ("http://localhost:8890", '"http://localhost:8890"'),
            ("-ctk q4_0", '"-ctk" q4_0'),
            ("systemd-coredump foot", '"systemd-coredump" foot'),
            ('foo "unclosed', 'foo unclosed'),
            ('title:foo', 'title:"foo"'),
            ('"exact phrase"', '"exact phrase"'),
        ]
        for query, expected in cases:
            self.assertEqual(sanitize_fts5_query(query), expected)

class TestSearchEngine(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA recursive_triggers = ON;")
        self.conn.executescript(SCHEMA_SQL)
        self.engine = SearchEngine(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_search_and_triggers(self):
        # Insert test session and messages
        self.conn.execute("""
            INSERT INTO sessions(uid, host, agent, native_id, title, project)
            VALUES ('host1:agy:s1', 'host1', 'agy', 's1', 'Terminal Debugging', '/home/user/work')
        """)
        self.conn.execute("""
            INSERT INTO messages(session_uid, host, agent, role, content, timestamp)
            VALUES ('host1:agy:s1', 'host1', 'agy', 'user', 'Check error 0000:00:14.0 on systemd-coredump', 1000.0)
        """)
        self.conn.commit()

        # Query with technical symbols
        results = self.engine.search("0000:00:14.0")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["session_uid"], "host1:agy:s1")

        results2 = self.engine.search("systemd-coredump")
        self.assertEqual(len(results2), 1)

        # Test session update trigger
        self.conn.execute("""
            UPDATE sessions SET title = 'Updated Title' WHERE uid = 'host1:agy:s1'
        """)
        self.conn.commit()

        results3 = self.engine.search("Updated")
        self.assertEqual(len(results3), 1)
        self.assertEqual(results3[0]["session_title"], "Updated Title")

        # Test CASCADE delete cleans up FTS
        self.conn.execute("DELETE FROM sessions WHERE uid = 'host1:agy:s1'")
        self.conn.commit()

        cur = self.conn.cursor()
        cur.execute("SELECT count(*) FROM messages_fts")
        self.assertEqual(cur.fetchone()[0], 0)

    def test_cmd_purge(self):
        from agent_search.cli import cmd_purge
        from unittest.mock import MagicMock

        # Insert records for two hosts
        self.conn.execute("INSERT INTO sessions(uid, host, agent, native_id, title) VALUES ('h1:1', 'host1', 'hermes', '1', 'T1')")
        self.conn.execute("INSERT INTO messages(session_uid, host, agent, role, content) VALUES ('h1:1', 'host1', 'hermes', 'user', 'M1')")
        self.conn.execute("INSERT INTO sessions(uid, host, agent, native_id, title) VALUES ('h2:1', 'host2', 'hermes', '1', 'T2')")
        self.conn.execute("INSERT INTO messages(session_uid, host, agent, role, content) VALUES ('h2:1', 'host2', 'hermes', 'user', 'M2')")
        self.conn.commit()

        args = MagicMock()
        args.host = "host1"
        args.yes = True

        cmd_purge(args, self.conn)

        cur = self.conn.cursor()
        cur.execute("SELECT count(*) FROM sessions WHERE host = 'host1'")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT count(*) FROM messages WHERE host = 'host1'")
        self.assertEqual(cur.fetchone()[0], 0)

        # host2 should remain intact
        cur.execute("SELECT count(*) FROM sessions WHERE host = 'host2'")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT count(*) FROM messages WHERE host = 'host2'")
        self.assertEqual(cur.fetchone()[0], 1)

class TestAntigravityIngestor(unittest.TestCase):
    def test_transcript_jsonl_parsing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            conv_id = "test-conv-uuid"
            logs_dir = base_dir / "brain" / conv_id / ".system_generated" / "logs"
            logs_dir.mkdir(parents=True)

            transcript_path = logs_dir / "transcript.jsonl"
            lines = [
                {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "How to fix foot crash?", "created_at": "2026-10-03T10:00:00Z"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "content": "Foot crashed due to NULL surface in ime.c", "created_at": "2026-10-03T10:00:02Z"}
            ]
            with open(transcript_path, "w") as f:
                for line in lines:
                    f.write(json.dumps(line) + "\n")

            # Create mock summaries DB
            db_path = base_dir / "conversation_summaries.db"
            sconn = sqlite3.connect(db_path)
            sconn.execute("""
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    preview TEXT,
                    step_count INTEGER,
                    last_modified_time TEXT,
                    workspace_uris TEXT
                )
            """)
            sconn.execute("""
                INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?)
            """, (conv_id, "Foot Crash Session", "preview", 2, "2026-10-03T10:00:02Z", '["file:///home/user/src"]'))
            sconn.commit()
            sconn.close()

            # Destination DB
            dest_conn = sqlite3.connect(":memory:")
            dest_conn.executescript(SCHEMA_SQL)

            ingestor = AntigravityIngestor(host="test-host", base_dir=str(base_dir))
            indexed = ingestor.ingest(dest_conn)

            self.assertEqual(indexed, 2)
            cur = dest_conn.cursor()
            cur.execute("SELECT count(*) FROM messages WHERE agent = 'agy'")
            self.assertEqual(cur.fetchone()[0], 2)
            dest_conn.close()

class TestServerAuth(unittest.TestCase):
    def test_auth_enforcement(self):
        import urllib.request
        import urllib.error
        import threading
        from agent_search.server import AgentSearchServer, AgentSearchHandler

        with tempfile.NamedTemporaryFile(suffix=".db") as tf:
            # Bind ephemeral port on localhost
            server = AgentSearchServer(("127.0.0.1", 0), AgentSearchHandler, db_path=tf.name, auth_token="secret123")
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()

            try:
                # 1. Unauthenticated request should fail with 401
                url = f"http://127.0.0.1:{port}/health"
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(url)
                self.assertEqual(ctx.exception.code, 401)
                ctx.exception.close()

                # 2. Authenticated request with Bearer header should succeed
                req = urllib.request.Request(url, headers={"Authorization": "Bearer secret123"})
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)

                # 3. Authenticated request with ?token= query param should succeed
                url_token = f"http://127.0.0.1:{port}/health?token=secret123"
                with urllib.request.urlopen(url_token) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                server.shutdown()
                server.server_close()

if __name__ == "__main__":
    unittest.main()

