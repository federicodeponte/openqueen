#!/usr/bin/env python3
"""
openqueen monitor — persisted detached process that watches a task PID,
pings WA with backoff, and releases the lock + dequeues when done.
Survives dispatch.py death.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
import subprocess

def _load_config() -> dict:
    cfg_path = OQ_HOME / "config.json"
    try:
        cfg = __import__("json").loads(cfg_path.read_text())
        cfg["log_dir"] = str(Path(cfg.get("log_dir", "~/openqueen/logs")).expanduser())
        return cfg
    except Exception:
        return {"log_dir": str(Path("~/openqueen/logs").expanduser())}

_CONFIG = _load_config()
LOGS_DIR = Path(_CONFIG["log_dir"])
OQ_HOME  = LOGS_DIR.parent
QUEUE_FILE = OQ_HOME / "QUEUE.json"
SEND_URL = "http://127.0.0.1:19234/send"
OQ_HOME = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))


def send_wa(text: str, image: str = None):
    payload = {"text": text}
    if image:
        payload["image"] = image
    try:
        data = json.dumps(payload).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                SEND_URL, data=data, headers={"Content-Type": "application/json"}
            ),
            timeout=10,
        )
    except Exception as e:
        print(f"[monitor] send_wa error: {e}", file=sys.stderr)


def send_telegram(text: str):
    token = os.environ.get("OQ_TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("OQ_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[monitor] Telegram not configured", file=sys.stderr)
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}),
            timeout=10,
        )
    except Exception as e:
        print(f"[monitor] send_telegram error: {e}", file=sys.stderr)


def notify(text: str, image: str = None):
    transport = os.environ.get("OQ_TRANSPORT", "whatsapp")
    if transport == "telegram":
        send_telegram(text)
    else:
        notify(text, image)



def get_api_key() -> str:
    try:
        for line in Path("/etc/environment").read_text().splitlines():
            if line.startswith("GOOGLE_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("GOOGLE_API_KEY", "")


def release_lock(task_name: str):
    (LOGS_DIR / f"RUNNING-{task_name}.lock").unlink(missing_ok=True)


def get_all_locks() -> list[dict]:
    locks = []
    for f in LOGS_DIR.glob("RUNNING-*.lock"):
        try:
            data = json.loads(f.read_text())
            pid = data.get("pid")
            if pid and Path(f"/proc/{pid}").exists():
                locks.append(data)
            else:
                f.unlink(missing_ok=True)
        except Exception:
            f.unlink(missing_ok=True)
    return locks


def dequeue_and_start():
    if not QUEUE_FILE.exists():
        return
    try:
        queue = json.loads(QUEUE_FILE.read_text())
    except Exception:
        return
    running_projects = {l.get("project_path") for l in get_all_locks()}
    remaining = []
    for item in queue:
        proj = item.get("project_path", "")
        if proj not in running_projects:
            _start_queued(item)
            running_projects.add(proj)
        else:
            remaining.append(item)
    if remaining:
        QUEUE_FILE.write_text(json.dumps(remaining, indent=2))
    else:
        QUEUE_FILE.unlink(missing_ok=True)


def _start_queued(item: dict):
    env = {**os.environ, "GOOGLE_API_KEY": get_api_key()}
    try:
        proc = subprocess.Popen(
            ["openqueen", item["task_file"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True,
        )
    except Exception as e:
        notify(f"Failed to start queued task: {e}")
        return
    lock_data = {
        "task": item["task_name"], "summary": item["summary"],
        "pid": proc.pid, "started": int(time.time()),
        "project_path": item.get("project_path", ""),
    }
    (LOGS_DIR / f"RUNNING-{item['task_name']}.lock").write_text(json.dumps(lock_data))
    label = item["summary"] or item["task_name"]
    notify(f"Starting now: _{label}_")
    # Spawn a new monitor for this queued task
    subprocess.Popen(
        ["python3", str(OQ_HOME / "monitor.py"),
         "--pid", str(proc.pid), "--task-name", item["task_name"],
         "--summary", item.get("summary", ""), "--started", str(int(time.time()))],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def find_task_log(task_name: str) -> str:
    import glob as _glob
    matches = sorted(_glob.glob(str(LOGS_DIR / f"{task_name}-*.log")), reverse=True)
    return matches[0] if matches else ""


def summarize_log_with_gemini(log_text: str, summary: str) -> str | None:
    try:
        import google.genai as genai
        api_key = get_api_key()
        if not api_key:
            return None
        client = genai.Client(api_key=api_key)
        tail = "\n".join(log_text.splitlines()[-30:])
        prompt = (
            f"Task: {summary}\n\nAgent log (last 30 lines):\n{tail}\n\n"
            "In 1-2 plain English sentences, describe what went wrong. "
            "No code, no file paths unless essential. Written for a non-technical user."
        )
        response = client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
        return response.text.strip()
    except Exception:
        return None


def wait_for_pid(pid: int):
    """Block until process PID is gone."""
    proc_path = Path(f"/proc/{pid}")
    while proc_path.exists():
        time.sleep(5)


def _cleanup_dispatch_logs(keep: int = 5):
    """Prune old dispatch-*.log files, keep the most recent `keep`."""
    import glob as _glob
    files = sorted(
        _glob.glob(str(LOGS_DIR / "dispatch-*.log")),
        key=lambda f: Path(f).stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            Path(old).unlink()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--started", type=float, default=None)
    args = parser.parse_args()

    pid = args.pid
    task_name = args.task_name
    summary = args.summary
    started = args.started or time.time()

    # Ping loop in foreground (this is the whole process)
    intervals = [300, 600, 900]
    default_interval = 1200
    idx = 0
    proc_path = Path(f"/proc/{pid}")

    while proc_path.exists():
        wait = intervals[idx] if idx < len(intervals) else default_interval
        idx += 1
        deadline = time.time() + wait
        while time.time() < deadline:
            if not proc_path.exists():
                break
            time.sleep(5)
        if proc_path.exists():
            elapsed = int((time.time() - started) / 60)
            label = summary or task_name
            notify(f"Still working on: _{label}_ ({elapsed} min in)...")

    # Task finished
    release_lock(task_name)
    _cleanup_dispatch_logs()
    dequeue_and_start()

    # Check exit code via log (best effort)
    log_path = find_task_log(task_name)
    try:
        log_text = Path(log_path).read_text() if log_path else ""
        if "WhatsApp: sent" in log_text:
            return  # openqueen already notified
        if log_text and "DONE:" not in log_text:
            human = summarize_log_with_gemini(log_text, summary)
            label = summary or task_name
            if human:
                notify(f"Something went wrong — {human}\n\nTry again or rephrase the task.")
            else:
                notify(f"Something went wrong with: _{label}_\n\nTry again or rephrase the task.")
    except Exception:
        pass


if __name__ == "__main__":
    main()
