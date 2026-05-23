"""Tests for the Codex CLI session scanner."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
CODEX_SESSION = os.path.join(FIXTURES, "codex_session.jsonl")


def _make_db():
    from token_dashboard.db import init_db
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.db")
    init_db(db_path)
    return db_path


class CodexScanFileTests(unittest.TestCase):
    def setUp(self):
        self.db_path = _make_db()

    def test_imports_two_turns_as_four_rows(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            result = scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        self.assertEqual(result["messages"], 4)  # 2 user + 2 assistant rows

    def test_session_id_matches_meta(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute("SELECT DISTINCT session_id FROM messages")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], "sess-codex-1")

    def test_source_is_codex(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM messages")}
        self.assertEqual(sources, {"codex"})

    def test_first_turn_token_counts(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            row = dict(conn.execute(
                "SELECT * FROM messages WHERE type='assistant' AND session_id='sess-codex-1' ORDER BY timestamp ASC LIMIT 1"
            ).fetchone())
        self.assertEqual(row["input_tokens"], 200)   # 1000 - 800 cached
        self.assertEqual(row["output_tokens"], 50)
        self.assertEqual(row["cache_read_tokens"], 800)
        self.assertEqual(row["cache_create_5m_tokens"], 0)
        self.assertEqual(row["cache_create_1h_tokens"], 0)

    def test_second_turn_uses_last_not_total(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM messages WHERE type='assistant' AND session_id='sess-codex-1' ORDER BY timestamp ASC"
            )]
        self.assertEqual(len(rows), 2)
        # Second turn: last_token_usage (1500/70), not total (2500/120); input net of 1200 cached = 300
        self.assertEqual(rows[1]["input_tokens"], 300)  # 1500 - 1200 cached
        self.assertEqual(rows[1]["output_tokens"], 70)

    def test_user_prompt_text_captured(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT prompt_text FROM messages WHERE type='user' ORDER BY timestamp ASC"
            )]
        self.assertEqual(rows[0]["prompt_text"], "hello world\n")
        self.assertEqual(rows[1]["prompt_text"], "second prompt\n")

    def test_model_on_assistant_rows(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            models = {r[0] for r in conn.execute(
                "SELECT DISTINCT model FROM messages WHERE type='assistant'"
            )}
        self.assertEqual(models, {"gpt-5.3-codex"})

    def test_project_slug_derived_from_cwd(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            slugs = {r[0] for r in conn.execute("SELECT DISTINCT project_slug FROM messages")}
        self.assertEqual(len(slugs), 1)
        slug = next(iter(slugs))
        # cwd "C:\Users\test\myproject" → "C--Users-test-myproject"
        self.assertIn("myproject", slug)

    def test_idempotent_rescan(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(count, 4)

    def test_end_offset_after_last_complete_turn(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        file_size = os.path.getsize(CODEX_SESSION)
        with connect(self.db_path) as conn:
            result = scan_file(Path(CODEX_SESSION), conn)
        self.assertGreater(result["end_offset"], 0)
        self.assertLessEqual(result["end_offset"], file_size)

    def test_assistant_parent_is_user_row(self):
        from token_dashboard.codex_scanner import scan_file
        from token_dashboard.db import connect
        with connect(self.db_path) as conn:
            scan_file(Path(CODEX_SESSION), conn)
            conn.commit()
        with connect(self.db_path) as conn:
            asst = dict(conn.execute(
                "SELECT * FROM messages WHERE type='assistant' ORDER BY timestamp ASC LIMIT 1"
            ).fetchone())
            user = conn.execute(
                "SELECT uuid FROM messages WHERE type='user' ORDER BY timestamp ASC LIMIT 1"
            ).fetchone()
        self.assertEqual(asst["parent_uuid"], user["uuid"])


class CodexScanDirTests(unittest.TestCase):
    def _make_sessions_dir(self, subdir="2026/05/02"):
        import shutil
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, *subdir.split("/"))
        os.makedirs(dest)
        shutil.copy(CODEX_SESSION, os.path.join(dest, "rollout-test.jsonl"))
        return tmp

    def test_scan_dir_finds_session(self):
        from token_dashboard.codex_scanner import scan_dir
        sessions_root = self._make_sessions_dir()
        db_path = _make_db()
        result = scan_dir(sessions_root, db_path)
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["messages"], 4)

    def test_scan_dir_skips_unchanged_file(self):
        from token_dashboard.codex_scanner import scan_dir
        sessions_root = self._make_sessions_dir()
        db_path = _make_db()
        scan_dir(sessions_root, db_path)
        result = scan_dir(sessions_root, db_path)
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["messages"], 0)

    def test_scan_dir_missing_root_returns_empty(self):
        from token_dashboard.codex_scanner import scan_dir
        db_path = _make_db()
        result = scan_dir("/nonexistent/path", db_path)
        self.assertEqual(result["messages"], 0)
        self.assertEqual(result["files"], 0)


class ParseRecordSourceTests(unittest.TestCase):
    def test_parse_record_includes_source_claude(self):
        from token_dashboard.scanner import parse_record
        rec = {
            "type": "assistant", "uuid": "u1", "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 5, "output_tokens": 3}},
        }
        msg, _ = parse_record(rec, project_slug="p")
        self.assertEqual(msg["source"], "claude")


class MigrateAddSourceTests(unittest.TestCase):
    def test_migration_adds_source_to_existing_db(self):
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "old.db")
        # Create DB with all current columns except source (simulates pre-source schema)
        with sqlite3.connect(db_path) as c:
            c.executescript("""
                CREATE TABLE files (path TEXT PRIMARY KEY, mtime REAL NOT NULL, bytes_read INTEGER NOT NULL, scanned_at REAL NOT NULL);
                CREATE TABLE messages (
                    uuid TEXT PRIMARY KEY, parent_uuid TEXT,
                    session_id TEXT NOT NULL, project_slug TEXT NOT NULL,
                    cwd TEXT, git_branch TEXT, cc_version TEXT, entrypoint TEXT,
                    type TEXT NOT NULL, is_sidechain INTEGER NOT NULL DEFAULT 0,
                    agent_id TEXT, timestamp TEXT NOT NULL, model TEXT, stop_reason TEXT,
                    prompt_id TEXT, message_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0,
                    prompt_text TEXT, prompt_chars INTEGER, tool_calls_json TEXT
                );
                CREATE TABLE tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, message_uuid TEXT NOT NULL,
                    session_id TEXT NOT NULL, project_slug TEXT NOT NULL,
                    tool_name TEXT NOT NULL, target TEXT, result_tokens INTEGER,
                    is_error INTEGER NOT NULL DEFAULT 0, timestamp TEXT NOT NULL
                );
                CREATE TABLE plan (k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE dismissed_tips (tip_key TEXT PRIMARY KEY, dismissed_at REAL NOT NULL);
            """)
            c.execute(
                "INSERT INTO messages (uuid,session_id,project_slug,type,timestamp,message_id) VALUES (?,?,?,?,?,?)",
                ('u1','s1','proj','assistant','2026-01-01','m1')
            )
        from token_dashboard.db import init_db, connect
        init_db(db_path)
        with connect(db_path) as c:
            row = c.execute("SELECT source FROM messages WHERE uuid='u1'").fetchone()
        self.assertEqual(row["source"], "claude")

    def test_migration_idempotent_when_column_exists(self):
        from token_dashboard.db import init_db
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "test.db")
        init_db(db_path)
        init_db(db_path)  # Should not raise


if __name__ == "__main__":
    unittest.main()
