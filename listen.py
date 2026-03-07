#!/usr/bin/env python3
"""
openqueen-listen v3 — watches OpenClaw session files for !task triggers.

Two detection methods:
  1. Queue file: /opt/clawdbot/data/openqueen-queue.json (written by clawdbot agent)
  2. Session files: detect new agent-replies containing "Queued: <path>"
     or new user messages starting with "!task " (when groupPolicy=open processes them)

This daemon runs on the HOST and spawns openqueen when a task is detected.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

QUEUE_FILE = Path("/opt/clawdbot/data/openqueen-queue.json")
SESSIONS_DIR = Path("/opt/clawdbot/data/agents/main/sessions")
STATE_FILE = Path("/root/.openqueen-listen-state.json")
BRIDGE = Path("/root/queen/whatsapp_bridge.py")
POLL_INTERVAL = 15  # seconds

RUNS_DIR = Path("/root/openqueen/logs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(RUNS_DIR / "listen.log")),
    ],
)
logger = logging.getLogger("oq-listen")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_seen_ts": "", "processed_sessions": []}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_whatsapp(msg: str):
    try:
        result = subprocess.run(
            [sys.executable, str(BRIDGE), "send", msg],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"WA sent: {msg[:80]}")
        else:
            logger.error(f"WA send failed: {result.stderr[:100]}")
    except Exception as e:
        logger.error(f"WA send error: {e}")


def get_api_key() -> str:
    try:
        for line in Path("/etc/environment").read_text().splitlines():
            if line.startswith("GOOGLE_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("GOOGLE_API_KEY", "")


def run_openqueen(task_path: str) -> int:
    api_key = get_api_key()
    if not api_key:
        logger.error("GOOGLE_API_KEY not found")
        return -1

    expanded = str(Path(task_path.strip()).expanduser())
    if not Path(expanded).exists():
        logger.error(f"Task file not found: {expanded}")
        send_whatsapp(f"openqueen: task file not found: {task_path}")
        return -1

    ts = int(time.time())
    log_file = str(RUNS_DIR / f"listen-run-{ts}.log")
    env = {**os.environ, "GOOGLE_API_KEY": api_key}

    proc = subprocess.Popen(
        ["openqueen", expanded],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    logger.info(f"Spawned openqueen PID={proc.pid} task={expanded} log={log_file}")
    return proc.pid


# ── Pattern matching ──────────────────────────────────────────────────────────

TASK_PREFIX = "!task"

def extract_task_path(text: str) -> str | None:
    """Extract path from '!task <path>' in text."""
    text = text.strip()
    if TASK_PREFIX in text:
        idx = text.find(TASK_PREFIX)
        remainder = text[idx + len(TASK_PREFIX):].strip()
        # Take first non-empty token (the path)
        path = remainder.split()[0] if remainder.split() else ""
        if path:
            return path
    return None


# ── Poll queue file ───────────────────────────────────────────────────────────

def poll_queue():
    if not QUEUE_FILE.exists():
        return
    try:
        content = QUEUE_FILE.read_text().strip()
        if not content:
            return
        entry = json.loads(content)
    except Exception as e:
        logger.error(f"Queue parse error: {e}")
        QUEUE_FILE.unlink(missing_ok=True)
        return

    task_path = entry.get("task_path", "").strip()
    logger.info(f"Queue file: task_path={task_path}")
    QUEUE_FILE.unlink(missing_ok=True)

    if task_path:
        pid = run_openqueen(task_path)
        if pid > 0:
            send_whatsapp(f"openqueen: started {Path(task_path).name} (PID={pid}). Will notify when done.")


# ── Poll session files ────────────────────────────────────────────────────────

def poll_sessions(state: dict):
    """Watch session files for new user messages with !task commands.

    When groupPolicy=open and a group message comes in, clawdbot creates
    an agent session. The incoming WhatsApp message appears as a 'user' role
    in that session. We detect these and trigger openqueen directly.
    """
    if not SESSIONS_DIR.exists():
        return

    last_ts = state.get("last_seen_ts", "")
    processed = set(state.get("processed_sessions", []))

    session_files = sorted(
        SESSIONS_DIR.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    new_last_ts = last_ts
    found_tasks = []

    for sf in session_files[:20]:
        if sf.stem in processed and sf.stat().st_mtime < time.time() - 300:
            continue  # Skip old processed sessions
        try:
            with open(sf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") != "message":
                            continue
                        ts = entry.get("timestamp", "")
                        if ts <= last_ts:
                            continue
                        msg = entry.get("message", {})
                        role = msg.get("role", "")
                        content = msg.get("content", [])

                        # Extract text
                        text = ""
                        if isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict) and c.get("type") == "text":
                                    text = c.get("text", "")
                                    break
                        elif isinstance(content, str):
                            text = content

                        if not text:
                            continue

                        if ts > new_last_ts:
                            new_last_ts = ts

                        # Only look at user messages (incoming WhatsApp)
                        if role == "user" and TASK_PREFIX in text:
                            path = extract_task_path(text)
                            if path:
                                found_tasks.append((ts, path, sf.stem))
                                logger.info(f"Found !task in session {sf.stem[:8]}: path={path}")

                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            logger.debug(f"Session read error {sf.name}: {e}")

    state["last_seen_ts"] = new_last_ts

    for ts, path, session_id in found_tasks:
        if session_id in processed:
            continue
        processed.add(session_id)
        logger.info(f"Running task from session {session_id[:8]}: {path}")
        pid = run_openqueen(path)
        if pid > 0:
            send_whatsapp(f"openqueen: started {Path(path).name} (PID={pid}). Will notify when done.")

    state["processed_sessions"] = list(processed)[-50:]  # Keep last 50


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("openqueen-listen v3 started")
    logger.info(f"Queue file: {QUEUE_FILE}")
    logger.info(f"Sessions dir: {SESSIONS_DIR}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")

    state = load_state()
    if not state.get("last_seen_ts"):
        state["last_seen_ts"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        logger.info(f"Initialized last_seen_ts={state['last_seen_ts']}")

    while True:
        try:
            poll_queue()
            poll_sessions(state)
            save_state(state)
        except Exception as e:
            logger.error(f"Poll error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
