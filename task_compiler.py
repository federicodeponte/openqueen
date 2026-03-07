#!/usr/bin/env python3
"""
openqueen task_compiler — turns natural language into a task.md file.
Uses Claude to infer project, context_keys, and done-when criteria.
Prints the path to the generated task.md (or nothing on failure).
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECTS_FILE = Path("/root/openqueen/projects.json")
TASKS_DIR = Path("/tmp/oq-tasks")
TASKS_DIR.mkdir(parents=True, exist_ok=True)

CONTEXT_KEYS = ["global:stack", "global:backend", "global:frontend", "global:user_flows"]


def load_projects() -> list[dict]:
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text())
    return []


def read_project_context(proj: dict) -> str:
    """Read CLAUDE.md and key files to give compiler context about the project."""
    path = Path(proj["path"]).expanduser()
    ctx = f"Project: {proj['name']}\nPath: {path}\nDescription: {proj.get('description', '')}\n"

    for filename in ["CLAUDE.md", "README.md"]:
        f = path / filename
        if f.exists():
            content = f.read_text()[:2000]
            ctx += f"\n### {filename}\n{content}\n"
            break

    # Try to detect tech stack
    for f in ["package.json", "requirements.txt", "pyproject.toml"]:
        fp = path / f
        if fp.exists():
            ctx += f"\n### {f}\n{fp.read_text()[:500]}\n"
            break

    return ctx


def build_prompt(nl: str, projects: list[dict]) -> str:
    project_list = "\n".join(
        f"- {p['name']}: {p.get('description', '')} (path: {p['path']})"
        for p in projects
    )

    project_contexts = "\n\n".join(read_project_context(p) for p in projects)

    available_context_keys = "\n".join(f"  - {k}" for k in CONTEXT_KEYS)

    return f"""You are a task compiler for openqueen, an AI coding orchestrator.
Your job: convert a natural language coding request into a task.md file.

## Available Projects
{project_list}

## Project Details
{project_contexts}

## Available Context Keys
{available_context_keys}

## User Request
{nl}

## Output Format
Output ONLY valid YAML frontmatter + markdown task body. No explanation, no code fences.

---
name: <slug-no-spaces-max-30-chars>
path: <absolute path to the project>
worker: claude
max_iterations: 10
context_keys:
  - <only include relevant ones>
---

## Task
<2-3 sentences describing exactly what to implement, referencing specific files if known>

## Done When
- [ ] <concrete verifiable criterion 1>
- [ ] <concrete verifiable criterion 2>
- [ ] <concrete verifiable criterion 3 if needed>

Rules:
- name: lowercase, hyphens only, describes the change
- path: must be an absolute path from the project list above
- Done When: must be checkable with a bash command (curl, pytest, grep, test -f)
- If the request is ambiguous about which project, pick the most likely one
- Do NOT include context_keys that are not relevant (e.g. no frontend key for a pure backend task)
"""


def call_claude(prompt: str) -> str:
    """Call claude -p with the prompt via stdin."""
    result = subprocess.run(
        ["claude", "-p", "--model", "claude-sonnet-4-6"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude error: {result.stderr[:300]}")
    return result.stdout.strip()


def parse_task_md(content: str) -> dict | None:
    """Validate the generated task.md has required frontmatter."""
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end == -1:
        return None
    try:
        import yaml
        frontmatter = yaml.safe_load(content[3:end])
        return frontmatter if isinstance(frontmatter, dict) else None
    except Exception:
        # Minimal manual parse
        fm = {}
        for line in content[3:end].splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        return fm if fm else None


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    nl = " ".join(sys.argv[1:]).strip()
    projects = load_projects()

    if not projects:
        print("No projects configured in /root/openqueen/projects.json", file=sys.stderr)
        sys.exit(1)

    prompt = build_prompt(nl, projects)

    try:
        content = call_claude(prompt)
    except Exception as e:
        print(f"Claude call failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Strip markdown fences if Claude wrapped it
    content = re.sub(r"^```(?:yaml|markdown)?\n?", "", content)
    content = re.sub(r"\n?```$", "", content)
    content = content.strip()

    # Validate
    fm = parse_task_md(content)
    if not fm or not fm.get("name") or not fm.get("path"):
        print(f"Invalid task.md generated:\n{content[:500]}", file=sys.stderr)
        sys.exit(1)

    # Ensure path is absolute
    task_path = Path(fm["path"]).expanduser()
    if not task_path.is_absolute():
        print(f"Path not absolute: {fm['path']}", file=sys.stderr)
        sys.exit(1)

    # Write to temp file
    ts = int(time.time())
    out_path = TASKS_DIR / f"{fm['name']}-{ts}.md"
    out_path.write_text(content)

    print(str(out_path))  # stdout = path for dispatch.py


if __name__ == "__main__":
    main()
