#!/usr/bin/env python3
"""
openqueen-listen v4 — watches OpenClaw session files for !task triggers.

Two detection methods:
  1. Queue file: /opt/clawdbot/data/openqueen-queue.json (written by clawdbot agent)
  2. Session files: detect new agent-replies containing "Queued: <path>"
     or new user messages starting with "!task " (when groupPolicy=open processes them)

This daemon runs on the HOST and spawns openqueen when a task is detected.
"""

import json
import urllib.request
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

OQ_HOME = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))
QUEUE_FILE = Path(os.environ.get("OQ_QUEUE_FILE", "/opt/clawdbot/data/openqueen-queue.json"))
SESSIONS_DIR = Path(os.environ.get("OQ_SESSIONS_DIR", "/opt/clawdbot/data/agents/main/sessions"))
STATE_FILE = Path(os.environ.get("OQ_STATE_FILE", str(Path.home() / ".openqueen-listen-state.json")))
BRIDGE = Path(os.environ.get("OQ_BRIDGE", "/root/queen/whatsapp_bridge.py"))
POLL_INTERVAL = 15  # seconds

RUNS_DIR = OQ_HOME / "logs"
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

def compile_nl_task(nl: str) -> str | None:
    """Compile a natural-language request to a task file path via lib.compiler."""
    import sys as _sys
    oq_home = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))
    _sys.path.insert(0, str(oq_home))
    try:
        from lib.compiler import compile_task
    except ImportError:
        logger.error("Cannot import lib.compiler — is OPENQUEEN_HOME correct?")
        return None
    try:
        task_path = compile_task(nl, api_key=get_api_key())
        return task_path
    except Exception as e:
        logger.error(f"compile_task error: {e}")
        return None


def poll_queue():
    if not QUEUE_FILE.exists():
        return
    try:
        raw = QUEUE_FILE.read_text().strip()
        if not raw:
            return
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"Queue parse error: {e}")
        QUEUE_FILE.unlink(missing_ok=True)
        return

    QUEUE_FILE.unlink(missing_ok=True)

    # Support both legacy single-entry format and dict-of-entries written by wa-listener
    entries: list[dict] = []
    if isinstance(data, dict):
        # Dict of entries: {"task-1234": {"nl": ..., "ts": ...}, ...}
        # OR legacy single entry: {"task_path": "/..."}
        if "task_path" in data or "nl" in data:
            entries = [data]
        else:
            entries = list(data.values())
    elif isinstance(data, list):
        entries = data

    for entry in entries:
        nl = (entry.get("nl") or "").strip()
        task_path = (entry.get("task_path") or "").strip()

        if nl:
            logger.info(f"Queue: NL task: {nl[:80]}")
            task_path = compile_nl_task(nl)
            if task_path:
                pid = run_openqueen(task_path)
                if pid > 0:
                    send_whatsapp(f"openqueen: started {Path(task_path).name} (PID={pid}). Will notify when done.")
            else:
                logger.error("NL compile failed — no task started")
        elif task_path:
            logger.info(f"Queue: task_path={task_path}")
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




# ── Telegram transport ────────────────────────────────────────────────────────

def _notify_telegram(text: str):
    """Send a message via Telegram Bot API. Requires OQ_TELEGRAM_TOKEN and OQ_TELEGRAM_CHAT_ID."""
    token = os.environ.get("OQ_TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("OQ_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram not configured: missing OQ_TELEGRAM_TOKEN or OQ_TELEGRAM_CHAT_ID")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}),
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Telegram send error: {e}")


def handle_nl_task(text: str):
    """Compile a natural-language task request and run openqueen on it."""
    import sys as _sys
    oq_home = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))
    _sys.path.insert(0, str(oq_home))
    try:
        from lib.compiler import compile_task
    except ImportError:
        logger.error("Cannot import lib.compiler — is OPENQUEEN_HOME correct?")
        _notify_telegram("Internal error: compiler not found.")
        return

    _notify_telegram("Compiling task...")
    try:
        task_path = compile_task(text, api_key=get_api_key())
    except Exception as e:
        logger.error(f"compile_task error: {e}")
        _notify_telegram(f"Compilation failed: {e}")
        return

    if not task_path:
        _notify_telegram(
            "I couldn't figure out exactly what to do. "
            "Try being more specific — mention the project name and what needs to change."
        )
        return

    logger.info(f"Compiled task: {task_path}")
    pid = run_openqueen(task_path)
    if pid > 0:
        from pathlib import Path as _Path
        _notify_telegram(
            f"Got it — starting *{_Path(task_path).stem}* (PID={pid}). Will notify when done."
        )
    else:
        _notify_telegram("Failed to start agent. Check logs.")


def run_telegram_listener():
    """Long-poll Telegram Bot API for incoming messages and dispatch tasks."""
    token = os.environ.get("OQ_TELEGRAM_TOKEN", "")
    chat_id = str(os.environ.get("OQ_TELEGRAM_CHAT_ID", ""))
    if not token or not chat_id:
        logger.error("OQ_TELEGRAM_TOKEN and OQ_TELEGRAM_CHAT_ID required for Telegram transport")
        sys.exit(1)

    logger.info(f"openqueen-listen (telegram) started, chat_id={chat_id}")
    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=30"
            with urllib.request.urlopen(url, timeout=35) as r:
                data = json.loads(r.read())

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                from_chat = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()

                if from_chat != chat_id:
                    continue
                if not text or text.startswith("/"):
                    continue

                logger.info(f"Telegram: received task request: {text[:80]}")
                handle_nl_task(text)

        except Exception as e:
            logger.warning(f"Telegram poll error: {e}")
            time.sleep(5)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    transport = os.environ.get("OQ_TRANSPORT", "whatsapp")
    logger.info(f"openqueen-listen v4 started (transport={transport})")

    if transport == "telegram":
        run_telegram_listener()
        return

    # WhatsApp / clawdbot path
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
