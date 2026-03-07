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
Output ONLY the task.md content below. No explanation, no code fences, nothing else.

# Task: <slug-lowercase-hyphens-max-30-chars>

## Project
path: <absolute expanded path to the project, no ~ >
worker: claude
context:
  - <only include relevant context keys from the list above>

## Objective
<3-5 sentences describing exactly what to implement or check. Reference specific files if known.>

## Done When
- <bash command that verifies criterion 1, e.g. grep -q 'pattern' path/to/file>
- <bash command that verifies criterion 2, e.g. curl -s localhost:PORT/endpoint | grep -q 'expected'>
- <bash command that verifies criterion 3 if needed>

Rules:
- path: must be the absolute path (expand ~ to /root or /home/user)
- Done When: must be real bash one-liners that return 0 on success
- If the request is ambiguous about which project, pick the most likely one
- Do NOT include context keys not relevant to the task
- For audit/readiness tasks: Done When should check key files exist, build passes, env vars set
"""


def call_claude(prompt: str) -> str:
    """Call Gemini to generate the task.md."""
    import google.genai as genai
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
    )
    return response.text.strip()


def parse_task_md(content: str) -> dict | None:
    """Validate the generated task.md has required sections."""
    fm = {}
    for line in content.splitlines():
        if line.startswith("# Task:"):
            fm["name"] = line.replace("# Task:", "").strip()
        if line.strip().startswith("path:"):
            fm["path"] = line.split("path:", 1)[1].strip()
    return fm if fm.get("name") and fm.get("path") else None


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

    task_path = Path(fm["path"]).expanduser()

    # Write to temp file
    ts = int(time.time())
    out_path = TASKS_DIR / f"{fm['name']}-{ts}.md"
    out_path.write_text(content)

    print(str(out_path))  # stdout = path for dispatch.py


if __name__ == "__main__":
    main()
