#!/usr/bin/env python3
"""
openqueen-listen — watches the OpenClaw queue file for task requests.

Flow:
  1. Federico sends "@openclaw !task ~/openqueen/tasks/foo.md" in WhatsApp group
  2. Clawdbot (groupPolicy:open, requireMention:true) picks it up, runs agent turn
  3. Agent reads openqueen skill, writes task path to queue file
  4. This daemon detects the queue file, runs openqueen, clears the queue

Queue file: /opt/clawdbot/data/openqueen-queue.json (shared container/host volume)
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

QUEUE_FILE = Path("/opt/clawdbot/data/openqueen-queue.json")
BRIDGE = Path("~/queen/whatsapp_bridge.py").expanduser()
RUNS_DIR = Path("~/openqueen/logs").expanduser()
POLL_INTERVAL = 15  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/root/openqueen/logs/listen.log"),
    ],
)
logger = logging.getLogger("oq-listen")


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


def run_openqueen(task_path: str) -> int:
    """Spawn openqueen on AX41 host. Returns PID."""
    # Get API key from /etc/environment
    try:
        env_content = Path("/etc/environment").read_text()
        api_key = ""
        for line in env_content.splitlines():
            if line.startswith("GOOGLE_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
                break
    except Exception:
        api_key = os.environ.get("GOOGLE_API_KEY", "")

    if not api_key:
        logger.error("GOOGLE_API_KEY not found in /etc/environment")
        return -1

    expanded = str(Path(task_path.strip()).expanduser())
    ts = int(time.time())
    log_file = f"/root/openqueen/logs/listen-run-{ts}.log"

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


def poll():
    if not QUEUE_FILE.exists():
        return

    try:
        content = QUEUE_FILE.read_text().strip()
        if not content:
            return
        entry = json.loads(content)
    except Exception as e:
        logger.error(f"Failed to read queue: {e}")
        return

    task_path = entry.get("task_path", "").strip()
    queued_ts = entry.get("ts", "")
    logger.info(f"Queue entry: task={task_path} ts={queued_ts}")

    if not task_path:
        QUEUE_FILE.unlink(missing_ok=True)
        return

    expanded = str(Path(task_path).expanduser())
    if not Path(expanded).exists():
        logger.error(f"Task file not found: {expanded}")
        send_whatsapp(f"openqueen error: task file not found: {task_path}")
        QUEUE_FILE.unlink(missing_ok=True)
        return

    # Clear queue before running (idempotent)
    QUEUE_FILE.unlink(missing_ok=True)

    pid = run_openqueen(task_path)
    if pid > 0:
        send_whatsapp(f"openqueen: started {Path(task_path).name} (PID={pid}). Will notify when done.")
    else:
        send_whatsapp(f"openqueen: failed to start {task_path} (no API key?)")


def main():
    logger.info("openqueen-listen v2 started")
    logger.info(f"Queue file: {QUEUE_FILE}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")

    while True:
        try:
            poll()
        except Exception as e:
            logger.error(f"Poll error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
