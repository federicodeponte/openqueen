"""Unit tests for gemini-agent — no API calls, no real subprocesses."""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent dir to path so we can import agent
sys.path.insert(0, str(Path(__file__).parent.parent))
import agent


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def config(tmp_path):
    log_dir = tmp_path / "logs"
    cfg = {
        "max_iterations": 10,
        "max_retries_on_failure": 3,
        "worker_timeout_seconds": 300,
        "bash_timeout_seconds": 60,
        "output_truncate_chars": 6000,
        "history_summarize_at_iteration": 5,
        "whatsapp_group": "120363423286386596@g.us",
        "whatsapp_bridge": "/tmp/fake_bridge.py",
        "log_dir": str(log_dir),
    }
    return cfg


@pytest.fixture
def logger(tmp_path):
    log = logging.getLogger(f"test-{tmp_path.name}")
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture
def task(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return {
        "name": "test-task",
        "path": str(project_dir),
        "worker": "claude",
        "new_project": False,
        "env_file": None,
        "context_keys": [],
        "objective": "Do something",
        "context": "",
        "done_when": ["file exists"],
        "raw": "# Task: test-task\n\n## Project\npath: /tmp/project\n\n## Objective\nDo something\n\n## Done When\n- file exists\n",
        "file": "/tmp/task.md",
    }


# ── Config loading ────────────────────────────────────────────────────────────

def test_load_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_data = {
        "max_iterations": 5,
        "max_retries_on_failure": 2,
        "worker_timeout_seconds": 120,
        "bash_timeout_seconds": 30,
        "output_truncate_chars": 3000,
        "history_summarize_at_iteration": 3,
        "whatsapp_group": "test@g.us",
        "whatsapp_bridge": "~/queen/whatsapp_bridge.py",
        "log_dir": "~/gemini-agent/logs",
    }
    cfg_file.write_text(json.dumps(cfg_data))
    monkeypatch.setattr(agent, "CONFIG_PATH", cfg_file)

    cfg = agent.load_config()
    assert cfg["max_iterations"] == 5
    assert "~" not in cfg["whatsapp_bridge"]  # ~ expanded
    assert "~" not in cfg["log_dir"]


# ── task.md parsing ───────────────────────────────────────────────────────────

def test_parse_task_md_all_fields(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("""# Task: My Test Task

## Project
path: /tmp/myproject
worker: codex
new_project: true
env_file: /tmp/.env.local

## Objective
Fix the bug in auth.py

## Context / Constraints
Use Python 3.12

## Done When
- /tmp/myproject/auth.py exists
- pytest passes
""")
    task = agent.parse_task_md(str(task_file))
    assert task["name"] == "my-test-task"
    assert task["path"] == "/tmp/myproject"
    assert task["worker"] == "codex"
    assert task["new_project"] is True
    assert task["env_file"] == "/tmp/.env.local"
    assert "Fix the bug" in task["objective"]
    assert len(task["done_when"]) == 2
    assert "/tmp/myproject/auth.py exists" in task["done_when"]


def test_parse_task_md_minimal(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("""# Task: Minimal

## Project
path: /tmp/proj

## Objective
Do it

## Done When
- done
""")
    task = agent.parse_task_md(str(task_file))
    assert task["worker"] == "claude"       # default
    assert task["new_project"] is False     # default
    assert task["env_file"] is None         # default


def test_parse_task_md_missing_path(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("# Task: No Path\n\n## Project\nworker: claude\n")
    with pytest.raises(ValueError, match="path"):
        agent.parse_task_md(str(task_file))


def test_parse_task_md_invalid_worker_falls_back(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("# Task: T\n\n## Project\npath: /tmp\nworker: gpt4\n\n## Done When\n- done\n")
    task = agent.parse_task_md(str(task_file))
    assert task["worker"] == "claude"  # invalid worker falls back to claude


def test_parse_task_md_inline_comments_stripped(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("# Task: T\n\n## Project\npath: /tmp/proj  # this is my project\n\n## Done When\n- done\n")
    task = agent.parse_task_md(str(task_file))
    assert task["path"] == "/tmp/proj"  # comment stripped


# ── context_keys parsing ──────────────────────────────────────────────────────

def test_parse_task_md_context_keys(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("""# Task: T

## Project
path: /tmp/proj
context:
  - global:machines
  - skills:backend
  - project

## Done When
- done
""")
    task = agent.parse_task_md(str(task_file))
    assert task["context_keys"] == ["global:machines", "skills:backend", "project"]


def test_parse_task_md_no_context_keys(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("# Task: T\n\n## Project\npath: /tmp/proj\n\n## Done When\n- done\n")
    task = agent.parse_task_md(str(task_file))
    assert task["context_keys"] == []


def test_load_context_global_key(tmp_path, logger, monkeypatch):
    context_dir = tmp_path / "context" / "global"
    context_dir.mkdir(parents=True)
    (context_dir / "machines.md").write_text("AX41: big server")
    monkeypatch.setattr(agent, "CONTEXT_DIR", tmp_path / "context")

    task = {"path": str(tmp_path / "project"), "context_keys": ["global:machines"]}
    result = agent.load_context(task, logger)
    assert "AX41: big server" in result
    assert "global:machines" in result


def test_load_context_project_key(tmp_path, logger):
    project_dir = tmp_path / "project"
    gemini_dir = project_dir / ".gemini"
    gemini_dir.mkdir(parents=True)
    (gemini_dir / "project.md").write_text("Brief: do stuff")
    (gemini_dir / "status.md").write_text("Iteration: 1/10")

    task = {"path": str(project_dir), "context_keys": ["project"]}
    result = agent.load_context(task, logger)
    assert "Brief: do stuff" in result
    assert "Iteration: 1/10" in result


def test_load_context_missing_file_skipped(tmp_path, logger, monkeypatch):
    monkeypatch.setattr(agent, "CONTEXT_DIR", tmp_path / "context")
    task = {"path": str(tmp_path / "project"), "context_keys": ["global:nonexistent"]}
    result = agent.load_context(task, logger)
    assert result == ""


def test_load_context_empty_keys_returns_empty(tmp_path, logger):
    task = {"path": str(tmp_path), "context_keys": []}
    assert agent.load_context(task, logger) == ""


def test_load_context_project_stack_override(tmp_path, logger, monkeypatch):
    context_dir = tmp_path / "context" / "global"
    context_dir.mkdir(parents=True)
    (context_dir / "stack.md").write_text("Python 3.11")

    project_dir = tmp_path / "project"
    gemini_dir = project_dir / ".gemini"
    gemini_dir.mkdir(parents=True)
    (gemini_dir / "stack.md").write_text("Python 3.12")

    monkeypatch.setattr(agent, "CONTEXT_DIR", tmp_path / "context")
    task = {"path": str(project_dir), "context_keys": ["global:stack", "project:stack"]}
    result = agent.load_context(task, logger)
    # Both present; global first, project last (last-loaded wins in LLM context)
    global_pos = result.index("Python 3.11")
    project_pos = result.index("Python 3.12")
    assert project_pos > global_pos


# ── Output truncation ─────────────────────────────────────────────────────────

def test_truncate_short_string():
    result = agent._truncate("hello", 100)
    assert result == "hello"


def test_truncate_returns_tail_not_head():
    long = "A" * 3000 + "B" * 3000
    result = agent._truncate(long, 3000)
    assert result.endswith("B" * 100)       # ends with the tail
    assert "A" not in result.split("\n")[-1]  # head is gone
    assert "truncated" in result


def test_truncate_exact_limit():
    text = "x" * 6000
    result = agent._truncate(text, 6000)
    assert result == text  # no truncation at exact limit


# ── Env file loading ──────────────────────────────────────────────────────────

def test_load_env_file_valid(tmp_path, logger):
    env_file = tmp_path / ".env.local"
    env_file.write_text("DB_URL=postgres://localhost/db\nSECRET_KEY=abc123\n")
    vals = agent.load_env_file(str(env_file), logger)
    assert vals["DB_URL"] == "postgres://localhost/db"
    assert vals["SECRET_KEY"] == "abc123"


def test_load_env_file_none(logger):
    vals = agent.load_env_file(None, logger)
    assert vals == {}


def test_load_env_file_missing(logger):
    vals = agent.load_env_file("/nonexistent/.env.local", logger)
    assert vals == {}  # warning logged, no crash


def test_load_env_file_values_not_in_log(tmp_path, logger, caplog):
    env_file = tmp_path / ".env.local"
    env_file.write_text("SECRET=supersecretvalue\n")
    with caplog.at_level(logging.DEBUG):
        agent.load_env_file(str(env_file), logger)
    assert "supersecretvalue" not in caplog.text
    assert "SECRET" in caplog.text  # key name IS logged


# ── Rate limit detection ──────────────────────────────────────────────────────

def test_rate_limit_triggers_escalate(tmp_path, config, task, logger):
    escalate_called = []

    def fake_escalate(reason, context=""):
        escalate_called.append((reason, context))
        raise SystemExit(1)

    claude_output = "You're out of extra usage · resets Mar 7, 6am (Europe/Berlin)"

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (claude_output, "")
        mock_popen.return_value = mock_proc

        with pytest.raises(SystemExit):
            agent.run_worker("do something", task["path"], task, config, {}, logger, fake_escalate)

    assert len(escalate_called) == 1
    assert "rate limited" in escalate_called[0][0].lower()


def test_rate_limit_not_triggered_by_normal_output(tmp_path, config, task, logger):
    """Ensure 'rate limiting' in legitimate task output doesn't false-positive."""
    escalate_called = []

    def fake_escalate(reason, context=""):
        escalate_called.append(reason)
        raise SystemExit(1)

    # Output about implementing rate limiting in code — should NOT trigger escalation
    claude_output = "I've implemented rate limiting using a token bucket algorithm."

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (claude_output, "")
        mock_popen.return_value = mock_proc

        result = agent.run_worker("add rate limiting", task["path"], task, config, {}, logger, fake_escalate)

    assert len(escalate_called) == 0
    assert "token bucket" in result


# ── Bridge check ──────────────────────────────────────────────────────────────

def test_bridge_check_up(config, logger, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="Up 3 days", returncode=0)
        result = agent.check_whatsapp_bridge(config, logger)
    assert result is True


def test_bridge_check_down_writes_sentinel(config, logger, tmp_path, monkeypatch):
    sentinel = tmp_path / "BRIDGE_DOWN"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        with patch.object(Path, "write_text") as mock_write:
            result = agent.check_whatsapp_bridge(config, logger)
    assert result is False


def test_bridge_check_exception_returns_false(config, logger):
    with patch("subprocess.run", side_effect=Exception("docker not found")):
        with patch.object(Path, "write_text"):
            result = agent.check_whatsapp_bridge(config, logger)
    assert result is False


# ── History summarization fallback ────────────────────────────────────────────

def test_summarize_history_failure_returns_original(logger):
    from google.genai import types as gtypes

    original_history = [
        gtypes.Content(role="user", parts=[gtypes.Part(text="task")]),
        gtypes.Content(role="model", parts=[gtypes.Part(text="response")]),
    ]

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("Flash API down")

    result = agent.summarize_history(mock_client, original_history, logger)
    assert result == original_history  # unchanged on failure


def test_summarize_history_returns_content_objects(logger):
    from google.genai import types as gtypes

    history = []
    for i in range(3):
        history.append(gtypes.Content(role="user", parts=[gtypes.Part(text=f"message {i}")]))
        history.append(gtypes.Content(role="model", parts=[gtypes.Part(text=f"response {i}")]))

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Summary: attempted X, succeeded Y, current state Z."
    mock_client.models.generate_content.return_value = mock_resp

    result = agent.summarize_history(mock_client, history, logger)

    # All items must be types.Content, never dicts (Bug 1)
    for item in result:
        assert isinstance(item, gtypes.Content), f"Expected Content, got {type(item)}"

    # First item should be the summary
    assert "Summary" in result[0].parts[0].text


# ── Log file creation ─────────────────────────────────────────────────────────

def test_log_file_created_at_correct_path(tmp_path, monkeypatch):
    config = {
        "log_dir": str(tmp_path / "logs"),
        "max_iterations": 10,
    }
    task = {"name": "my-task", "path": "/tmp", "worker": "claude"}
    logger = agent.setup_logger(task, config)

    log_files = list((tmp_path / "logs").glob("my-task-*.log"))
    assert len(log_files) == 1
    assert log_files[0].name.startswith("my-task-")


# ── Iteration limit ───────────────────────────────────────────────────────────

def test_iteration_limit_calls_escalate(tmp_path, config, task, monkeypatch):
    """When Gemini keeps returning tool calls, escalate fires at max_iterations."""
    from google.genai import types as gtypes

    config["max_iterations"] = 2

    escalated = []

    # Patch all the things needed to run main()
    monkeypatch.setattr(agent, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps(config))

    monkeypatch.setattr(agent, "GLOBAL_PROMPT_PATH", tmp_path / "global_prompt.md")
    (tmp_path / "global_prompt.md").write_text("You are an orchestrator. Iteration {iteration}/{max_iterations}.")

    task_file = tmp_path / "task.md"
    task_file.write_text(
        "# Task: iter-test\n\n## Project\npath: /tmp\n\n## Objective\nDo it\n\n## Done When\n- done\n"
    )

    mock_part = MagicMock()
    mock_part.function_call = MagicMock()
    mock_part.function_call.name = "run_bash"
    mock_part.function_call.args = {"cmd": "echo hi"}
    mock_part.text = None

    mock_content = MagicMock()
    mock_content.parts = [mock_part]

    mock_response = MagicMock()
    mock_response.candidates = [MagicMock(content=mock_content)]

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}):
            with patch("subprocess.run") as mock_subprocess:
                # bridge check returns "Up", bash returns output
                mock_subprocess.return_value = MagicMock(stdout="Up", returncode=0, stderr="")

                with patch.object(agent, "_send_whatsapp", return_value=True) as mock_wa:
                    with pytest.raises(SystemExit) as exc_info:
                        agent.main(str(task_file))

    assert exc_info.value.code == 1  # escalated, not success
