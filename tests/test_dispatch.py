"""Tests for dispatch.py — lock logic, queue, watchdog."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, "/root/openqueen")
import dispatch


@pytest.fixture(autouse=True)
def isolate_dispatch(tmp_oq_home, monkeypatch):
    logs = tmp_oq_home / "logs"
    monkeypatch.setattr(dispatch, "LOGS_DIR", logs)
    monkeypatch.setattr(dispatch, "OQ_HOME", tmp_oq_home)
    monkeypatch.setattr(dispatch, "QUEUE_FILE", tmp_oq_home / "QUEUE.json")
    monkeypatch.setattr(dispatch, "SESSIONS_DIR", logs / "sessions")
    monkeypatch.setattr(dispatch, "TRANSCRIPTS_DIR", logs / "transcripts")


def test_acquire_and_release_lock(tmp_oq_home):
    dispatch.acquire_lock("my-task", "Fix bug", pid=99999, project_path="/tmp/proj")
    lock_file = dispatch.LOGS_DIR / "RUNNING-my-task.lock"
    assert lock_file.exists()
    data = json.loads(lock_file.read_text())
    assert data["task"] == "my-task"
    assert data["pid"] == 99999
    dispatch.release_lock("my-task")
    assert not lock_file.exists()


def test_get_all_locks_prunes_dead_pids(tmp_oq_home):
    lock_file = dispatch.LOGS_DIR / "RUNNING-dead-task.lock"
    lock_file.write_text(json.dumps({"task": "dead-task", "pid": 99999999, "project_path": ""}))
    locks = dispatch.get_all_locks()
    assert not any(l["task"] == "dead-task" for l in locks)
    assert not lock_file.exists()


def test_get_all_locks_keeps_live_pids(tmp_oq_home):
    live_pid = os.getpid()
    lock_file = dispatch.LOGS_DIR / "RUNNING-live-task.lock"
    lock_file.write_text(json.dumps({"task": "live-task", "pid": live_pid, "project_path": "/tmp"}))
    locks = dispatch.get_all_locks()
    assert any(l["task"] == "live-task" for l in locks)
    assert lock_file.exists()


def test_is_locked_for_project(tmp_oq_home):
    live_pid = os.getpid()
    lock_file = dispatch.LOGS_DIR / "RUNNING-proj-task.lock"
    lock_file.write_text(json.dumps({
        "task": "proj-task", "pid": live_pid,
        "project_path": "/tmp/myproject", "summary": "Fix it"
    }))
    result = dispatch.is_locked_for_project("/tmp/myproject")
    assert result is not None
    assert result["task"] == "proj-task"
    assert dispatch.is_locked_for_project("/tmp/other") is None


def test_enqueue_and_dequeue(tmp_oq_home):
    dispatch.enqueue_task("/tmp/task.md", "my-task", "Fix it", "/tmp/proj")
    assert dispatch.QUEUE_FILE.exists()
    queue = json.loads(dispatch.QUEUE_FILE.read_text())
    assert len(queue) == 1
    assert queue[0]["task_name"] == "my-task"


def test_dequeue_skips_busy_project(tmp_oq_home):
    live_pid = os.getpid()
    lock_file = dispatch.LOGS_DIR / "RUNNING-busy.lock"
    lock_file.write_text(json.dumps({
        "task": "busy", "pid": live_pid,
        "project_path": "/tmp/busy-proj", "summary": "Still running"
    }))
    dispatch.enqueue_task("/tmp/t.md", "queued-task", "Wait for it", "/tmp/busy-proj")
    with patch.object(dispatch, "_start_task") as mock_start:
        dispatch.dequeue_and_start()
        mock_start.assert_not_called()
    queue = json.loads(dispatch.QUEUE_FILE.read_text())
    assert len(queue) == 1


def test_dequeue_starts_free_project(tmp_oq_home, tmp_path):
    task_file = tmp_path / "t.md"
    task_file.write_text("# Task: ready\n\n## Project\npath: /tmp\n")
    dispatch.enqueue_task(str(task_file), "ready-task", "Go", "/tmp/free-proj")
    with patch.object(dispatch, "_start_task") as mock_start:
        dispatch.dequeue_and_start()
        mock_start.assert_called_once()
    assert not dispatch.QUEUE_FILE.exists()


def test_parse_task_name_from_header(tmp_path):
    f = tmp_path / "my-task-1234567890.md"
    f.write_text("# Task: the-real-name\n\n## Project\npath: /tmp\n")
    assert dispatch.parse_task_name(str(f)) == "the-real-name"


def test_parse_task_name_falls_back_to_stem(tmp_path):
    f = tmp_path / "my-task-1234567890.md"
    f.write_text("## Project\npath: /tmp\n")
    name = dispatch.parse_task_name(str(f))
    assert name == "my-task"


def test_watchdog_releases_orphaned_lock(tmp_oq_home):
    lock_file = dispatch.LOGS_DIR / "RUNNING-orphan.lock"
    lock_file.write_text(json.dumps({
        "task": "orphan", "pid": 99999999,
        "project_path": "/tmp", "summary": "orphan"
    }))
    with patch.object(dispatch, "dequeue_and_start") as mock_dequeue:
        with patch.object(dispatch, "_spawn_monitor"):
            dispatch._watchdog()
    assert not lock_file.exists()
    mock_dequeue.assert_called_once()


def test_watchdog_respawns_dead_monitor(tmp_oq_home):
    live_pid = os.getpid()
    lock_file = dispatch.LOGS_DIR / "RUNNING-no-monitor.lock"
    lock_file.write_text(json.dumps({
        "task": "no-monitor", "pid": live_pid,
        "project_path": "/tmp", "summary": "running"
    }))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        with patch.object(dispatch, "_spawn_monitor") as mock_spawn:
            dispatch._watchdog()
            mock_spawn.assert_called_once()
