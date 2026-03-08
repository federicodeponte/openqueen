#!/usr/bin/env python3
"""
openqueen lib/compiler — turns natural language into a task.md file.
Importable: call compile_task(nl, api_key) -> path_str | None
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TASKS_DIR = Path("/tmp/oq-tasks")
TASKS_DIR.mkdir(parents=True, exist_ok=True)
OQ_HOME = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))


def _load_config() -> dict:
    cfg_path = OQ_HOME / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        cfg["log_dir"] = str(Path(cfg.get("log_dir", "~/openqueen/logs")).expanduser())
        return cfg
    except Exception:
        return {"log_dir": str(Path("~/openqueen/logs").expanduser())}


def load_projects() -> list[dict]:
    projects_file = OQ_HOME / "projects.json"
    if projects_file.exists():
        return json.loads(projects_file.read_text())
    # Auto-scan OQ_WORKSPACE for git repos (cap 50)
    workspace = os.environ.get("OQ_WORKSPACE", "")
    if not workspace:
        return []
    ws = Path(workspace).expanduser()
    if not ws.is_dir():
        return []
    projects = []
    for d in sorted(ws.iterdir()):
        if d.is_dir() and (d / ".git").exists():
            projects.append({
                "name": d.name,
                "path": str(d),
                "description": "",
            })
            if len(projects) >= 50:
                break
    return projects


def read_project_context(proj: dict) -> str:
    path = Path(proj["path"]).expanduser()
    ctx = f"Project: {proj['name']}\nPath: {path}\nDescription: {proj.get('description', '')}\n"
    for filename in ["CLAUDE.md", "README.md"]:
        f = path / filename
        if f.exists():
            ctx += f"\n### {filename}\n{f.read_text()[:10000]}\n"
            break
    for f in ["package.json", "requirements.txt", "pyproject.toml"]:
        fp = path / f
        if fp.exists():
            ctx += f"\n### {f}\n{fp.read_text()[:2000]}\n"
            break
    return ctx


def build_prompt(nl: str, projects: list[dict]) -> str:
    project_list = "\n".join(
        f"- {p['name']}: {p.get('description', '')} (path: {p['path']})"
        for p in projects
    )
    project_contexts = "\n\n".join(read_project_context(p) for p in projects)

    return f"""You are a task compiler for openqueen, an AI coding orchestrator.
Your job: convert a natural language coding request into a task.md file that a Claude worker will execute.

## Available Projects
{project_list}

## Project Details
{project_contexts}

## User Request
{nl}

## Output Format
Output ONLY the task.md content below. No explanation, no code fences, nothing else.

# Task: <slug-lowercase-hyphens-max-30-chars>

## Summary
<One plain English sentence for the user. E.g.: "Fix the loading spinner in OpenChat." Max 100 chars. No jargon.>

## Project
path: <absolute expanded path, no ~>
worker: claude
max_iterations: <8 simple, 12 multi-file, 15 complex>

## Objective
<3-5 sentences. Reference specific files and line numbers where known. One clear deliverable.>

## Done When
- <bash command that verifies a specific OUTPUT FILE exists: test -f /absolute/path/to/output.ext>
- <bash command that verifies file CONTENT: grep -q "expected string" /path/to/file>

Rules:
- Summary: one human sentence, no angle brackets
- path: absolute (expand ~ to /root on this machine)
- Done When: ONLY real bash that checks DELIVERABLES (output files, written content).
  NEVER check live site health (curl, ping).
  NEVER use placeholders. Every command must work as written.
  Prefer: test -f, grep -q, wc -l, python3 -c "import json; json.load(open(...))"
- Scope to what ONE worker can do in max_iterations
"""


def call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-opus-4-6", "--permission-mode", "dontAsk"],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude call failed (exit {result.returncode}): {result.stderr[:300]}")
    return result.stdout.strip()


def parse_task_md(content: str) -> dict | None:
    fm = {}
    done_when_lines = []
    in_done_when = False
    in_summary = False
    for line in content.splitlines():
        if line.startswith("# Task:"):
            fm["name"] = line.replace("# Task:", "").strip()
        if line.startswith("## Summary"):
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            in_summary = False
        if in_summary and line.strip() and not line.startswith("<"):
            fm["summary"] = line.strip()[:140]
            in_summary = False
        if line.strip().startswith("path:") and "path" not in fm:
            fm["path"] = line.split("path:", 1)[1].strip()
        if line.startswith("## Done When"):
            in_done_when = True
            continue
        if line.startswith("## ") and in_done_when:
            in_done_when = False
        if in_done_when and line.strip().startswith("-"):
            cmd = line.strip().lstrip("-").strip()
            if cmd and not cmd.startswith("<"):
                done_when_lines.append(cmd)
    fm["done_when_count"] = len(done_when_lines)
    if not fm.get("name") or not fm.get("path"):
        return None
    if fm["done_when_count"] == 0:
        return None
    return fm


def compile_task(nl: str, api_key: str = "") -> str | None:
    """Compile natural language to task.md. Returns file path or None on failure."""
    projects = load_projects()
    if not projects:
        print("[compiler] No projects found — set OQ_WORKSPACE or create projects.json", file=sys.stderr)
        return None

    env_override = {}
    if api_key:
        env_override["GOOGLE_API_KEY"] = api_key

    prompt = build_prompt(nl, projects)

    def attempt(extra: str = "") -> str | None:
        try:
            return call_claude(prompt + extra)
        except Exception as e:
            print(f"[compiler] Claude call failed: {e}", file=sys.stderr)
            return None

    content = attempt()
    if content:
        content = re.sub(r"^```(?:yaml|markdown)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
    fm = parse_task_md(content) if content else None

    if not fm:
        print("[compiler] First attempt invalid, retrying...", file=sys.stderr)
        content = attempt(
            "\n\nIMPORTANT: Previous response was invalid. "
            "Output ONLY the task.md. Summary must be one plain sentence (no angle brackets). "
            "Done When must be real bash commands. Absolute paths only."
        )
        if content:
            content = re.sub(r"^```(?:yaml|markdown)?\n?", "", content)
            content = re.sub(r"\n?```$", "", content).strip()
        fm = parse_task_md(content) if content else None

    if not fm:
        print(f"[compiler] Invalid task.md after retry:\n{(content or '')[:500]}", file=sys.stderr)
        return None

    task_path = Path(fm["path"]).expanduser()
    if not task_path.exists():
        print(f"[compiler] Project path does not exist: {task_path}", file=sys.stderr)
        return None

    ts = int(time.time())
    out_path = TASKS_DIR / f"{fm['name']}-{ts}.md"
    out_path.write_text(content)
    return str(out_path)
