"""Codex CLI session walker + parser.

Reads ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl files and ingests
completed turns into the shared messages table with source='codex'.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Union

from .db import connect
from .scanner import INSERT_MSG


def _project_slug(cwd: str) -> str:
    if not cwd:
        return "unknown"
    return re.sub(r"[:\\/ ]", "-", cwd)


def _make_uuid(session_id: str, turn_id: str, role: str) -> str:
    return f"codex:{session_id}:{turn_id}:{role}"


def scan_file(path: Path, conn, start_byte: int = 0) -> dict:
    """Ingest completed turns from a Codex session JSONL file.

    Returns {messages, tools, end_offset}. end_offset is the byte position
    after the last fully-processed task_complete line; the caller persists this
    as the high-water mark so incomplete turns at EOF are retried on next scan.
    """
    msgs = 0
    end_offset = start_byte

    with open(path, "rb") as fb:
        # session_meta is always the first line; read it regardless of start_byte
        first_raw = fb.readline()
        if not first_raw.endswith(b"\n"):
            return {"messages": 0, "tools": 0, "end_offset": start_byte}
        first_end = fb.tell()

        try:
            meta = json.loads(first_raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"messages": 0, "tools": 0, "end_offset": start_byte}

        if meta.get("type") != "session_meta":
            return {"messages": 0, "tools": 0, "end_offset": start_byte}

        meta_payload = meta.get("payload") or {}
        session_id = meta_payload.get("id")
        session_cwd = meta_payload.get("cwd")
        cli_version = meta_payload.get("cli_version")

        if not session_id:
            return {"messages": 0, "tools": 0, "end_offset": start_byte}

        # Seek to start_byte for incremental processing (but never before end of first line)
        fb.seek(max(start_byte, first_end))

        active_turn_id = None
        turns: dict = {}  # turn_id -> accumulated data

        while True:
            raw = fb.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                break  # partial line at EOF — retry on next scan
            line_end = fb.tell()

            try:
                rec = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if not isinstance(rec, dict):
                continue

            rec_type = rec.get("type")
            payload = rec.get("payload") or {}
            timestamp = rec.get("timestamp")

            if rec_type == "turn_context":
                turn_id = payload.get("turn_id")
                if turn_id and turn_id not in turns:
                    turns[turn_id] = {
                        "turn_id": turn_id,
                        "cwd": payload.get("cwd") or session_cwd,
                        "model": payload.get("model"),
                        "timestamp": timestamp,
                        "prompt_text": None,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                    }

            elif rec_type == "event_msg":
                evt = payload.get("type")

                if evt == "task_started":
                    active_turn_id = payload.get("turn_id")

                elif evt == "user_message":
                    if active_turn_id and active_turn_id in turns:
                        turns[active_turn_id]["prompt_text"] = payload.get("message")

                elif evt == "token_count":
                    info = payload.get("info")
                    if info and active_turn_id and active_turn_id in turns:
                        usage = info.get("last_token_usage") or {}
                        t = turns[active_turn_id]
                        cached = int(usage.get("cached_input_tokens") or 0)
                        t["input_tokens"] = max(0, int(usage.get("input_tokens") or 0) - cached)
                        t["output_tokens"] = int(usage.get("output_tokens") or 0)
                        t["cache_read_tokens"] = cached
                        t["timestamp"] = timestamp or t["timestamp"]

                elif evt == "task_complete":
                    turn_id = payload.get("turn_id")
                    if not turn_id or turn_id not in turns:
                        continue
                    turn = turns[turn_id]
                    ts = turn.get("timestamp")
                    if not ts:
                        end_offset = line_end
                        continue

                    slug = _project_slug(turn.get("cwd") or session_cwd or "")
                    user_uuid = _make_uuid(session_id, turn_id, "user")
                    asst_uuid = _make_uuid(session_id, turn_id, "assistant")

                    # Skip if already imported (idempotency)
                    if conn.execute("SELECT 1 FROM messages WHERE uuid=?", (asst_uuid,)).fetchone():
                        end_offset = line_end
                        continue

                    prompt_text = turn.get("prompt_text")
                    cwd = turn.get("cwd") or session_cwd

                    user_row = {
                        "uuid":                  user_uuid,
                        "parent_uuid":           None,
                        "session_id":            session_id,
                        "project_slug":          slug,
                        "cwd":                   cwd,
                        "git_branch":            None,
                        "cc_version":            cli_version,
                        "entrypoint":            "cli",
                        "type":                  "user",
                        "is_sidechain":          0,
                        "agent_id":              None,
                        "timestamp":             ts,
                        "model":                 None,
                        "stop_reason":           None,
                        "prompt_id":             None,
                        "message_id":            f"{session_id}:{turn_id}:user",
                        "input_tokens":          0,
                        "output_tokens":         0,
                        "cache_read_tokens":     0,
                        "cache_create_5m_tokens": 0,
                        "cache_create_1h_tokens": 0,
                        "prompt_text":           prompt_text,
                        "prompt_chars":          len(prompt_text) if prompt_text else None,
                        "tool_calls_json":       None,
                        "source":                "codex",
                    }

                    asst_row = {
                        "uuid":                  asst_uuid,
                        "parent_uuid":           user_uuid,
                        "session_id":            session_id,
                        "project_slug":          slug,
                        "cwd":                   cwd,
                        "git_branch":            None,
                        "cc_version":            cli_version,
                        "entrypoint":            "cli",
                        "type":                  "assistant",
                        "is_sidechain":          0,
                        "agent_id":              None,
                        "timestamp":             ts,
                        "model":                 turn.get("model"),
                        "stop_reason":           None,
                        "prompt_id":             None,
                        "message_id":            f"{session_id}:{turn_id}:assistant",
                        "input_tokens":          turn["input_tokens"],
                        "output_tokens":         turn["output_tokens"],
                        "cache_read_tokens":     turn["cache_read_tokens"],
                        "cache_create_5m_tokens": 0,
                        "cache_create_1h_tokens": 0,
                        "prompt_text":           None,
                        "prompt_chars":          None,
                        "tool_calls_json":       None,
                        "source":                "codex",
                    }

                    conn.execute(INSERT_MSG, user_row)
                    conn.execute(INSERT_MSG, asst_row)
                    msgs += 2
                    end_offset = line_end

    return {"messages": msgs, "tools": 0, "end_offset": end_offset}


def scan_dir(codex_sessions_root: Union[str, Path], db_path: Union[str, Path]) -> dict:
    """Walk YYYY/MM/DD/*.jsonl under codex_sessions_root and ingest new turns."""
    root = Path(codex_sessions_root)
    totals = {"messages": 0, "tools": 0, "files": 0}
    if not root.is_dir():
        return totals
    with connect(db_path) as conn:
        for p in root.rglob("*.jsonl"):
            try:
                stat = p.stat()
            except OSError:
                continue
            row = conn.execute(
                "SELECT mtime, bytes_read FROM files WHERE path=?", (str(p),)
            ).fetchone()
            if row and row["mtime"] == stat.st_mtime and row["bytes_read"] == stat.st_size:
                continue
            # Always read from beginning (session_meta needed); idempotency via uuid check
            sub = scan_file(p, conn, start_byte=0)
            conn.execute(
                "INSERT OR REPLACE INTO files (path, mtime, bytes_read, scanned_at) VALUES (?, ?, ?, ?)",
                (str(p), stat.st_mtime, sub["end_offset"], time.time()),
            )
            totals["messages"] += sub["messages"]
            totals["tools"]    += sub["tools"]
            totals["files"]    += 1
        conn.commit()
    return totals
