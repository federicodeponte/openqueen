"""Shared fixtures for OpenQueen tests. No real API calls."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "/root/openqueen")


@pytest.fixture
def tmp_oq_home(tmp_path, monkeypatch):
    home = tmp_path / "openqueen"
    home.mkdir()
    (home / "logs").mkdir()
    (home / "logs" / "sessions").mkdir()
    (home / "logs" / "transcripts").mkdir()
    cfg = {
        "max_iterations": 10,
        "worker_timeout_seconds": 300,
        "bash_timeout_seconds": 60,
        "output_truncate_chars": 6000,
        "history_max_chars": 60000,
        "whatsapp_group": "test@g.us",
        "log_dir": str(home / "logs"),
    }
    (home / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("OPENQUEEN_HOME", str(home))
    return home


@pytest.fixture
def tmp_projects(tmp_path):
    projects = []
    for name in ["project-a", "project-b"]:
        p = tmp_path / name
        p.mkdir()
        (p / ".git").mkdir()
        (p / "README.md").write_text(f"# {name}")
        projects.append({"name": name, "path": str(p), "description": f"Test {name}"})
    return projects


@pytest.fixture
def projects_json(tmp_oq_home, tmp_projects):
    (tmp_oq_home / "projects.json").write_text(json.dumps(tmp_projects, indent=2))
    return tmp_projects
