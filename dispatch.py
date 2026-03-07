#!/usr/bin/env python3
"""
openqueen dispatch — receives natural language from WhatsApp,
compiles to task.md via task_compiler.py, runs openqueen.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SEND_URL = "http://127.0.0.1:19234/send"
LOGS_DIR = Path("/root/openqueen/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def send_wa(text: str, image: str = None):
    payload = {"text": text}
    if image:
        payload["image"] = image
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", SEND_URL,
             "-H", "Content-Type: application/json",
             "-d", json.dumps(payload)],
            timeout=10,
            capture_output=True,
        )
    except Exception as e:
        print(f"[dispatch] send_wa error: {e}", file=sys.stderr)


def get_api_key() -> str:
    try:
        for line in Path("/etc/environment").read_text().splitlines():
            if line.startswith("GOOGLE_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("GOOGLE_API_KEY", "")


def compile_task(nl: str) -> str | None:
    result = subprocess.run(
        ["python3", "/root/openqueen/task_compiler.py", nl],
        capture_output=True, text=True, timeout=90,
        env={**os.environ, "GOOGLE_API_KEY": get_api_key()},
    )
    if result.returncode != 0:
        print(f"[dispatch] compiler error: {result.stderr[:300]}", file=sys.stderr)
        return None
    path = result.stdout.strip()
    return path if path and Path(path).exists() else None


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    nl_task = " ".join(sys.argv[1:]).strip()
    print(f"[dispatch] task: {nl_task[:100]}")

    send_wa("Building task...")

    task_path = compile_task(nl_task)
    if not task_path:
        send_wa("Could not build task. Try rephrasing — be more specific about the project and what you want.")
        return

    # Strip trailing timestamp (e.g. "openpaper-launch-readiness-1772906409" → "openpaper-launch-readiness")
    import re as _re
    task_name = _re.sub(r'-\d{10}$', '', Path(task_path).stem)
    ts = int(time.time())
    log_file = str(LOGS_DIR / f"run-{ts}.log")

    env = {**os.environ, "GOOGLE_API_KEY": get_api_key()}
    proc = subprocess.Popen(
        ["openqueen", task_path],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    send_wa(f"Started: {task_name} (PID={proc.pid})")
    print(f"[dispatch] spawned openqueen PID={proc.pid} log={log_file}")


if __name__ == "__main__":
    main()
