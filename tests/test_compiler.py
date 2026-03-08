"""Tests for lib/compiler.py — no real API calls."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, "/root/openqueen")
from lib.compiler import load_projects, parse_task_md, build_prompt


def test_load_projects_from_json(tmp_oq_home, projects_json, monkeypatch):
    from lib import compiler as comp
    monkeypatch.setattr(comp, "OQ_HOME", tmp_oq_home)
    projects = load_projects()
    assert len(projects) == 2
    assert projects[0]["name"] == "project-a"


def test_load_projects_no_json_no_workspace(tmp_oq_home, monkeypatch):
    from lib import compiler as comp
    monkeypatch.setattr(comp, "OQ_HOME", tmp_oq_home)
    monkeypatch.delenv("OQ_WORKSPACE", raising=False)
    projects = load_projects()
    assert projects == []


def test_load_projects_auto_scan_workspace(tmp_path, tmp_oq_home, monkeypatch):
    from lib import compiler as comp
    monkeypatch.setattr(comp, "OQ_HOME", tmp_oq_home)
    ws = tmp_path / "workspace"
    ws.mkdir()
    for name in ["repo-x", "repo-y", "plain-dir"]:
        d = ws / name
        d.mkdir()
        if name.startswith("repo"):
            (d / ".git").mkdir()
    monkeypatch.setenv("OQ_WORKSPACE", str(ws))
    projects = load_projects()
    names = [p["name"] for p in projects]
    assert "repo-x" in names
    assert "repo-y" in names
    assert "plain-dir" not in names


def test_load_projects_auto_scan_cap_50(tmp_path, tmp_oq_home, monkeypatch):
    from lib import compiler as comp
    monkeypatch.setattr(comp, "OQ_HOME", tmp_oq_home)
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(60):
        d = ws / f"repo-{i:02d}"
        d.mkdir()
        (d / ".git").mkdir()
    monkeypatch.setenv("OQ_WORKSPACE", str(ws))
    projects = load_projects()
    assert len(projects) <= 50


VALID_TASK = """\
# Task: fix-auth-bug

## Summary
Fix the authentication bug in the login flow.

## Project
path: /tmp/myproject
worker: claude
max_iterations: 12

## Objective
Fix the null pointer in auth.py line 42.

## Done When
- test -f /tmp/myproject/auth.py
- grep -q "fix applied" /tmp/myproject/auth.py
"""


def test_parse_valid_task(tmp_path):
    # parse_task_md takes content (string), not a file path
    result = parse_task_md(VALID_TASK)
    assert result is not None
    assert result["name"] == "fix-auth-bug"
    assert result["done_when_count"] == 2


def test_parse_task_no_name_returns_none():
    assert parse_task_md("## Project\npath: /tmp\n\n## Done When\n- test -f /tmp/x\n") is None


def test_parse_task_no_path_returns_none():
    assert parse_task_md("# Task: my-task\n\n## Done When\n- test -f /tmp/x\n") is None


def test_parse_task_no_done_when_returns_none():
    assert parse_task_md("# Task: my-task\n\n## Project\npath: /tmp\n") is None


def test_parse_task_placeholder_done_when_ignored():
    assert parse_task_md("# Task: t\n\n## Project\npath: /tmp\n\n## Done When\n- <bash command here>\n") is None


def test_build_prompt_contains_project_names(tmp_projects):
    prompt = build_prompt("fix the bug", tmp_projects)
    assert "project-a" in prompt
    assert "project-b" in prompt


def test_build_prompt_no_context_keys_section(tmp_projects):
    prompt = build_prompt("do something", tmp_projects)
    assert "context_keys" not in prompt.lower()
    assert "Available Context Keys" not in prompt


def test_build_prompt_done_when_rules_forbid_curl(tmp_projects):
    prompt = build_prompt("fix it", tmp_projects)
    assert "NEVER check live site health" in prompt
    assert "curl" in prompt


def test_compile_task_uses_import_not_subprocess(tmp_oq_home, projects_json):
    import dispatch
    import inspect
    src = inspect.getsource(dispatch.compile_task)
    assert "subprocess.run" not in src
    assert "lib.compiler" in src or "_compile" in src
