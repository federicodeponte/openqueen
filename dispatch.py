#!/usr/bin/env python3
"""
openqueen dispatch — receives natural language from WhatsApp,
compiles to task.md via task_compiler.py, runs openqueen.

Parallel tasks: different projects run concurrently; same project queues.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SEND_URL = "http://127.0.0.1:19234/send"
OQ_HOME = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))

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
SESSIONS_DIR = LOGS_DIR / "sessions"
TRANSCRIPTS_DIR = LOGS_DIR / "transcripts"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


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
        print(f"[dispatch] send_wa error: {e}", file=sys.stderr)


def get_api_key() -> str:
    try:
        for line in Path("/etc/environment").read_text().splitlines():
            if line.startswith("GOOGLE_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("GOOGLE_API_KEY", "")


# ---------------------------------------------------------------------------
# Per-task lock system
# ---------------------------------------------------------------------------

def get_lock_file(task_name: str) -> Path:
    return LOGS_DIR / f"RUNNING-{task_name}.lock"


def acquire_lock(task_name: str, summary: str, pid: int, project_path: str = ""):
    get_lock_file(task_name).write_text(json.dumps({
        "task": task_name,
        "summary": summary,
        "pid": pid,
        "started": int(time.time()),
        "project_path": project_path,
    }))


def release_lock(task_name: str):
    get_lock_file(task_name).unlink(missing_ok=True)


def get_all_locks() -> list[dict]:
    """Return all valid (process-alive) lock dicts; prune stale lock files."""
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


def is_locked_for_project(project_path: str) -> dict | None:
    """Return the lock dict if any running task uses this project path."""
    for lock in get_all_locks():
        if lock.get("project_path") == project_path:
            return lock
    return None


# ---------------------------------------------------------------------------
# Queue system (same-project serialization)
# ---------------------------------------------------------------------------

def enqueue_task(task_file: str, task_name: str, summary: str, project_path: str):
    queue = json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []
    queue.append({
        "task_file": task_file,
        "task_name": task_name,
        "summary": summary,
        "project_path": project_path,
        "queued_at": int(time.time()),
    })
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def dequeue_and_start():
    """After a task finishes, start any queued tasks whose project is now free."""
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
            _start_task(item["task_file"], item["task_name"], item["summary"], proj, queued=True)
            running_projects.add(proj)
        else:
            remaining.append(item)

    if remaining:
        QUEUE_FILE.write_text(json.dumps(remaining, indent=2))
    else:
        QUEUE_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Task compilation helpers
# ---------------------------------------------------------------------------

def compile_task(nl: str) -> str | None:
    sys.path.insert(0, str(OQ_HOME))
    from lib.compiler import compile_task as _compile
    return _compile(nl, api_key=get_api_key())


def parse_task_name(task_path: str) -> str:
    """Parse canonical task name from '# Task: slug' header. Falls back to filename stem."""
    try:
        for line in Path(task_path).read_text().splitlines():
            if line.startswith("# Task:"):
                name = line.replace("# Task:", "").strip()
                if name:
                    return name
    except Exception:
        pass
    return re.sub(r'-\d{10}$', '', Path(task_path).stem)


def extract_summary(task_path: str) -> str:
    try:
        content = Path(task_path).read_text()
        in_summary = False
        for line in content.splitlines():
            if line.startswith("## Summary"):
                in_summary = True
                continue
            if in_summary and line.startswith("## "):
                break
            if in_summary and line.strip() and not line.startswith("<"):
                return line.strip()[:140]
    except Exception:
        pass
    return ""


def extract_project_path(task_path: str) -> str:
    """Extract 'path:' value from the ## Project section of a task.md."""
    try:
        content = Path(task_path).read_text()
        in_project = False
        for line in content.splitlines():
            if line.startswith("## Project"):
                in_project = True
                continue
            if in_project and line.startswith("## "):
                break
            if in_project and line.strip().startswith("path:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


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
            f"Task: {summary}\n\n"
            f"Agent log (last 30 lines):\n{tail}\n\n"
            "In 1-2 plain English sentences, describe what the agent did or what went wrong. "
            "No code, no file paths unless essential. Written for a non-technical user."
        )
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
        )
        return response.text.strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status() -> str:
    locks = get_all_locks()
    queued = json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []

    if not locks and not queued:
        return "Nothing running — send me a task."

    lines = []
    for lock in locks:
        mins = round((time.time() - lock.get("started", time.time())) / 60)
        label = lock.get("summary") or lock.get("task", "unknown")
        lines.append(f"Running: _{label}_ ({mins} min)")

    for item in queued:
        label = item.get("summary") or item.get("task_name", "unknown")
        lines.append(f"Queued: _{label}_")

    return "\n".join(lines)


def cmd_stop() -> str:
    locks = get_all_locks()
    queued = json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []

    if not locks and not queued:
        return "Nothing is running."

    stopped = []
    for lock in locks:
        pid = lock.get("pid")
        label = lock.get("summary") or lock.get("task", "unknown")
        task_name = lock.get("task", "")
        try:
            import signal
            os.killpg(pid, signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, 15)
            except Exception:
                pass
        release_lock(task_name)
        stopped.append(label)

    # Clear the queue
    QUEUE_FILE.unlink(missing_ok=True)

    if len(stopped) == 1:
        return f"Stopped: _{stopped[0]}_"
    elif stopped:
        return f"Stopped {len(stopped)} tasks: " + ", ".join(f"_{s}_" for s in stopped)
    else:
        return "Queue cleared."


def cmd_log() -> str:
    locks = get_all_locks()
    if not locks:
        return "No task running — no log to read."

    # Show log for the most recently started task
    lock = sorted(locks, key=lambda l: l.get("started", 0), reverse=True)[0]
    task_name = lock.get("task", "")
    summary = lock.get("summary", "")

    log_file = ""
    try:
        import glob as _glob
        pattern = str(LOGS_DIR / f"{task_name}-*.log") if task_name else str(LOGS_DIR / "*.log")
        files = sorted(_glob.glob(pattern), key=lambda f: Path(f).stat().st_mtime, reverse=True)
        files = [f for f in files if not Path(f).name.startswith("dispatch-")]
        if files:
            log_file = files[0]
    except Exception:
        pass

    if not log_file:
        return "No log yet."

    try:
        log_text = Path(log_file).read_text()
        human = summarize_log_with_gemini(log_text, summary)
        if human:
            return human
        lines = [l for l in log_text.splitlines() if l.strip()]
        return "\n".join(lines[-5:])
    except Exception:
        return "Could not read log."


def _load_resumable_sessions() -> list[tuple[str, dict]]:
    """Return (file, state) pairs for sessions not currently running, newest first."""
    import glob as _glob
    running_tasks = {l.get("task") for l in get_all_locks()}
    results = []
    for f in sorted(_glob.glob(str(SESSIONS_DIR / "*.session.json")),
                    key=lambda f: Path(f).stat().st_mtime, reverse=True):
        try:
            state = json.loads(Path(f).read_text())
        except Exception:
            continue
        task_name = state.get("task_name", "")
        if task_name in running_tasks:
            continue
        if not state.get("task_raw") and not Path(state.get("task_file", "")).exists():
            continue
        results.append((f, state))
    return results


def cmd_resume() -> str:
    sessions = _load_resumable_sessions()
    if not sessions:
        return "No resumable sessions found."

    session_file, state = sessions[0]
    task_name = state.get("task_name", "unknown")
    label = state.get("summary") or task_name
    iter_done = state.get("iteration", 0)
    task_file = state.get("task_file", "")
    project_path = state.get("project_path", "")

    log_file = state.get("log_file", "/dev/null")
    out = open(log_file, "a")
    proc = subprocess.Popen(
        ["openqueen", "--resume", session_file],
        stdout=out, stderr=out,
        env={**os.environ, "GOOGLE_API_KEY": get_api_key()},
        start_new_session=True,
    )
    out.close()

    acquire_lock(task_name, label, proc.pid, project_path)

    extra = ""
    if len(sessions) > 1:
        others = ", ".join(s.get("summary") or s.get("task_name", "?") for _, s in sessions[1:3])
        extra = f"\n({len(sessions)-1} other interrupted: {others})"

    send_wa(f"Resuming: _{label}_ (from iteration {iter_done + 1})...{extra}")
    _spawn_monitor(proc.pid, task_name, label, time.time())
    return ""

def _start_task(task_file: str, task_name: str, summary: str, project_path: str, queued: bool = False):
    """Spawn openqueen for a task and set up monitoring."""
    env = {**os.environ, "GOOGLE_API_KEY": get_api_key()}
    try:
        proc = subprocess.Popen(
            ["openqueen", task_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except Exception as e:
        send_wa(f"Failed to start agent: {e}")
        return

    started = time.time()
    acquire_lock(task_name, summary, proc.pid, project_path)

    if queued:
        first = summary[0].lower() + summary[1:] if summary else "queued task"
        send_wa(f"Starting now: _{first}_")
    else:
        if summary:
            first = summary[0].lower() + summary[1:]
            send_wa(f"Got it — {first}.")
        else:
            send_wa("Got it, working on it...")

    print(f"[dispatch] spawned PID={proc.pid} task={task_name} project={project_path}")

    _spawn_monitor(proc.pid, task_name, summary, started)


def _spawn_monitor(pid: int, task_name: str, summary: str, started: float):
    """Spawn monitor.py as a detached process — survives dispatch.py death."""
    subprocess.Popen(
        ["python3", str(OQ_HOME / "monitor.py"),
         "--pid", str(pid), "--task-name", task_name,
         "--summary", summary, "--started", str(int(started))],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _watchdog():
    """Re-spawn monitor.py for any running task whose monitor died.
    Also cleans up locks for tasks whose process has already exited."""
    for f in list(LOGS_DIR.glob("RUNNING-*.lock")):
        try:
            lock = json.loads(f.read_text())
        except Exception:
            f.unlink(missing_ok=True)
            continue
        pid = lock.get("pid")
        task_name = lock.get("task", "")
        summary = lock.get("summary", "")
        started = lock.get("started", time.time())
        if not pid or not task_name:
            f.unlink(missing_ok=True)
            continue
        if not Path(f"/proc/{pid}").exists():
            # Process is gone — release lock and dequeue (monitor must have died before doing so)
            print(f"[dispatch] watchdog: task {task_name} finished without cleanup, releasing lock", file=sys.stderr)
            f.unlink(missing_ok=True)
            dequeue_and_start()
            continue
        # Process alive — check if monitor is watching it
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"monitor.py.*--pid.*{pid}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"[dispatch] watchdog: re-spawning monitor for {task_name} (pid={pid})", file=sys.stderr)
                _spawn_monitor(pid, task_name, summary, float(started))
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    nl_task = " ".join(sys.argv[1:]).strip()

    if nl_task == "__status__":
        send_wa(cmd_status())
        return
    if nl_task == "__stop__":
        send_wa(cmd_stop())
        return
    if nl_task == "__log__":
        send_wa(cmd_log())
        return
    if nl_task == "__resume__":
        msg = cmd_resume()
        if msg:
            send_wa(msg)
        return

    # Watchdog: re-spawn any dead monitors before doing new work
    _watchdog()

    print(f"[dispatch] task: {nl_task[:100]}")

    # If argument is an existing task file path, use it directly (skip compilation)
    if nl_task.startswith("/") and Path(nl_task).exists():
        task_path = nl_task
        print(f"[dispatch] using pre-written task file: {task_path}")
    else:
        # Compile the task to a task.md file
        task_path = compile_task(nl_task)
        if not task_path:
            send_wa(
                "I couldn't figure out exactly what to do. "
                "Try being more specific — mention the project name and what needs to change."
            )
            return

    task_name = parse_task_name(task_path)
    summary = extract_summary(task_path)
    project_path = extract_project_path(task_path)

    # Check if this project is already running
    existing = is_locked_for_project(project_path) if project_path else None

    if existing:
        # Same project is busy — queue this task
        enqueue_task(task_path, task_name, summary, project_path)
        running_label = existing.get("summary") or existing.get("task", "unknown")
        queue_label = summary or task_name
        send_wa(
            f"Got it — _{queue_label}_ queued.\n"
            f"Will start once _{running_label}_ finishes."
        )
        print(f"[dispatch] queued task={task_name} behind project={project_path}")
        return

    # Different project (or no project) — start immediately
    _start_task(task_path, task_name, summary, project_path)


if __name__ == "__main__":
    main()
