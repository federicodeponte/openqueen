#!/usr/bin/env python3
"""
openqueen — CLI entry point.

Usage:
  openqueen init          Interactive setup wizard
  openqueen status        Show running tasks and recent logs
  openqueen run <task>    Run a task.md file directly
  openqueen logs [n]      Tail last n lines of dispatch log (default 50)
  openqueen version       Show version
"""
import os
import subprocess
import sys
from pathlib import Path

OQ_HOME = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))
VERSION = "0.1.0"


def cmd_init():
    init_script = OQ_HOME / "init.py"
    if not init_script.exists():
        print(f"Error: {init_script} not found", file=sys.stderr)
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, str(init_script)])


def cmd_status():
    import json
    from datetime import datetime

    queue_file = OQ_HOME / "QUEUE.json"
    logs_dir = OQ_HOME / "logs" / "sessions"

    print(f"\nOpenQueen Status  (home: {OQ_HOME})\n")

    # Active tasks from QUEUE.json
    if queue_file.exists():
        try:
            queue = json.loads(queue_file.read_text())
            active = {k: v for k, v in queue.items() if v.get("status") in ("running", "queued")}
            if active:
                print(f"  Active tasks ({len(active)}):")
                for proj, info in active.items():
                    print(f"    {proj}: {info.get('status')} — {info.get('task_name', '?')}")
            else:
                print("  No active tasks")
        except Exception:
            print("  Could not read queue")
    else:
        print("  Queue file not found")

    # Recent sessions
    if logs_dir.exists():
        sessions = sorted(logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        if sessions:
            print(f"\n  Recent sessions:")
            for s in sessions:
                mtime = datetime.fromtimestamp(s.stat().st_mtime).strftime("%m-%d %H:%M")
                print(f"    {mtime}  {s.name}")
    print()


def cmd_run(task_file: str):
    agent = OQ_HOME / "agent.py"
    venv_python = OQ_HOME / ".venv" / "bin" / "python3"
    python = str(venv_python) if venv_python.exists() else sys.executable
    os.execv(python, [python, str(agent), task_file])


def cmd_logs(n: int = 50):
    logs_dir = OQ_HOME / "logs" / "sessions"
    if not logs_dir.exists():
        print("No logs directory found")
        sys.exit(1)
    sessions = sorted(logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not sessions:
        print("No session logs found")
        sys.exit(1)
    latest = sessions[0]
    print(f"==> {latest} <==\n")
    lines = latest.read_text().splitlines()
    for line in lines[-n:]:
        print(line)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    if cmd == "init":
        cmd_init()
    elif cmd == "status":
        cmd_status()
    elif cmd == "run":
        if len(args) < 2:
            print("Usage: openqueen run <task.md>", file=sys.stderr)
            sys.exit(1)
        cmd_run(args[1])
    elif cmd == "logs":
        n = int(args[1]) if len(args) > 1 else 50
        cmd_logs(n)
    elif cmd == "version":
        print(f"openqueen {VERSION}")
    else:
        print(f"Unknown command: {cmd}\n{__doc__}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
