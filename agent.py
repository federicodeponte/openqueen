#!/usr/bin/env python3
"""
Gemini Agent — Gemini 3.1 Pro orchestrates Claude/Codex workers autonomously.
Usage: openqueen <task.md>
"""

import json
import logging
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from google import genai
from google.genai import types

# ── Constants ─────────────────────────────────────────────────────────────────

AGENT_DIR = Path("~/openqueen").expanduser()
CONFIG_PATH = AGENT_DIR / "config.json"
GLOBAL_PROMPT_PATH = AGENT_DIR / "global_prompt.md"
CONTEXT_DIR = AGENT_DIR / "context"

# Exact strings from Claude's rate limit output — specific to avoid false positives
RATE_LIMIT_STRINGS = [
    "you're out of extra usage",
    "you are out of extra usage",
    "claude.ai/settings/limits",
]

# ── Global state for signal handling ──────────────────────────────────────────

_current_proc: "subprocess.Popen | None" = None
_logger: "logging.Logger | None" = None


def _cleanup(signum, frame):
    if _logger:
        _logger.warning(f"Signal {signum} received — cleaning up")
    if _current_proc and _current_proc.poll() is None:
        try:
            os.killpg(os.getpgid(_current_proc.pid), signal.SIGTERM)
            if _logger:
                _logger.warning("Killed worker subprocess")
        except (ProcessLookupError, OSError):
            pass
    sys.exit(1)


signal.signal(signal.SIGTERM, _cleanup)
signal.signal(signal.SIGINT, _cleanup)

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    cfg["whatsapp_bridge"] = str(Path(cfg["whatsapp_bridge"]).expanduser())
    cfg["log_dir"] = str(Path(cfg["log_dir"]).expanduser())
    return cfg


# ── Task parsing ──────────────────────────────────────────────────────────────

def parse_task_md(task_file: str) -> dict:
    raw = Path(task_file).read_text()
    lines = raw.splitlines()

    task = {
        "name": "unnamed-task",
        "path": None,
        "worker": "claude",
        "new_project": False,
        "env_file": None,
        "context_keys": [],  # list of "namespace:key" or "project" strings
        "objective": "",
        "context": "",
        "done_when": [],
        "raw": raw,
        "file": task_file,
    }

    for line in lines:
        if line.startswith("# Task:"):
            task["name"] = line.replace("# Task:", "").strip().lower().replace(" ", "-")
            break

    in_project = False
    in_context_list = False
    for line in lines:
        if line.strip() == "## Project":
            in_project = True
            continue
        if line.startswith("## ") and in_project:
            in_project = False
            in_context_list = False
            continue
        if in_project:
            stripped = line.strip()
            if in_context_list:
                if stripped.startswith("-"):
                    task["context_keys"].append(stripped.lstrip("-").strip())
                    continue
                else:
                    in_context_list = False
            if ":" in stripped and not stripped.startswith("-"):
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.split("#")[0].strip()
                if key == "path":
                    task["path"] = str(Path(val).expanduser())
                elif key == "worker":
                    task["worker"] = val if val in ("claude", "codex") else "claude"
                elif key == "new_project":
                    task["new_project"] = val.lower() in ("true", "yes", "1")
                elif key == "env_file":
                    task["env_file"] = str(Path(val).expanduser()) if val else None
                elif key == "context" and not val:
                    in_context_list = True

    if not task["path"]:
        raise ValueError("task.md must have 'path:' under ## Project")

    current_section = None
    done_when_lines = []
    for line in lines:
        if line.startswith("## Objective"):
            current_section = "objective"
            continue
        elif line.startswith("## Context"):
            current_section = "context"
            continue
        elif line.startswith("## Done When"):
            current_section = "done_when"
            continue
        elif line.startswith("## "):
            current_section = None

        if current_section == "objective":
            task["objective"] += line + "\n"
        elif current_section == "context":
            task["context"] += line + "\n"
        elif current_section == "done_when" and line.strip().startswith("-"):
            done_when_lines.append(line.strip().lstrip("-").strip())

    task["objective"] = task["objective"].strip()
    task["context"] = task["context"].strip()
    task["done_when"] = done_when_lines
    return task


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logger(task: dict, config: dict) -> logging.Logger:
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"{task['name']}-{ts}.log"

    logger = logging.getLogger("openqueen")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%S")
    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    logger.info(f"Log file: {log_file}")
    return logger


# ── Env loading ───────────────────────────────────────────────────────────────

def load_env_file(env_file: "str | None", logger: logging.Logger) -> dict:
    if not env_file:
        return {}
    p = Path(env_file)
    if not p.exists():
        logger.warning(f"env_file not found: {env_file} — proceeding without it")
        return {}
    vals = dict(dotenv_values(p))
    logger.info(f"Loaded env_file: {env_file} — keys: {list(vals.keys())}")
    return vals


# ── Bridge check ──────────────────────────────────────────────────────────────

def check_whatsapp_bridge(config: dict, logger: logging.Logger) -> bool:
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=clawdbot", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "Up" in result.stdout:
            logger.info("WhatsApp bridge: UP")
            return True
        logger.warning("WhatsApp bridge: DOWN (clawdbot not running)")
    except Exception as e:
        logger.warning(f"WhatsApp bridge check failed: {e}")

    Path("~/openqueen/BRIDGE_DOWN").expanduser().write_text(
        f"Bridge down at {datetime.now().isoformat()}\n"
    )
    return False


def _send_whatsapp(message: str, config: dict, logger: logging.Logger) -> bool:
    try:
        result = subprocess.run(
            ["python3", config["whatsapp_bridge"], "send", message],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("WhatsApp: sent")
            return True
        logger.error(f"WhatsApp send failed: {result.stderr[:200]}")
    except Exception as e:
        logger.error(f"WhatsApp send exception: {e}")
    return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"[...truncated, showing last {limit} chars...]\n" + text[-limit:]


# ── Worker ────────────────────────────────────────────────────────────────────

def run_worker(
    prompt: str,
    cwd: str,
    task: dict,
    config: dict,
    env_vars: dict,
    logger: logging.Logger,
    escalate_fn,
) -> str:
    global _current_proc

    full_prompt = f"{task['raw']}\n\n---\n\n{prompt}"
    merged_env = {**os.environ, **env_vars}
    expanded_cwd = str(Path(cwd).expanduser())

    if not Path(expanded_cwd).exists():
        if task.get("new_project"):
            Path(expanded_cwd).mkdir(parents=True, exist_ok=True)
            logger.info(f"Created project directory: {expanded_cwd}")
        else:
            return f"ERROR: directory does not exist: {expanded_cwd}"

    worker = task["worker"]
    logger.info(f"Running {worker} in {expanded_cwd}")
    logger.debug(f"Prompt (first 300 chars): {prompt[:300]}")

    if worker == "claude":
        cmd = ["claude", "-p", "--permission-mode", "dontAsk", "--output-format", "text"]
        popen_cwd = expanded_cwd
    else:
        out_file = f"/tmp/codex-out-{uuid.uuid4().hex}.txt"
        cmd = [
            "codex", "exec", "-",
            "--dangerously-bypass-approvals-and-sandbox",
            "-s", "danger-full-access",
            "-C", expanded_cwd,
            "--output-last-message", out_file,
        ]
        popen_cwd = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=popen_cwd,
            env=merged_env,
            start_new_session=True,
        )
        _current_proc = proc
        stdout, stderr = proc.communicate(
            input=full_prompt,
            timeout=config["worker_timeout_seconds"],
        )
        _current_proc = None
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        _current_proc = None
        return f"ERROR: {worker} timed out after {config['worker_timeout_seconds']}s"

    if worker == "codex":
        try:
            output = Path(out_file).read_text()
            Path(out_file).unlink(missing_ok=True)
        except Exception:
            output = stdout + stderr
    else:
        output = stdout + (f"\nSTDERR: {stderr}" if stderr.strip() else "")

    logger.debug(f"Full {worker} output ({len(output)} chars):\n{output}")

    # Bug 4 fix: exact Claude-specific strings only, not broad "rate limit"
    if any(s in output.lower() for s in RATE_LIMIT_STRINGS):
        logger.error("Rate limit detected in worker output")
        escalate_fn(f"{worker} is rate limited", f"Output: {output[:300]}")
        return ""  # escalate_fn calls sys.exit — never reached

    return _truncate(output, config["output_truncate_chars"])


def run_bash_cmd(cmd: str, cwd: str, config: dict, logger: logging.Logger) -> str:
    expanded_cwd = str(Path(cwd).expanduser())
    logger.info(f"bash: {cmd[:120]}")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=expanded_cwd if Path(expanded_cwd).exists() else None,
            timeout=config["bash_timeout_seconds"],
        )
        output = result.stdout + (f"\nSTDERR: {result.stderr}" if result.stderr.strip() else "")
        return _truncate(output, config["output_truncate_chars"])
    except subprocess.TimeoutExpired:
        return f"ERROR: bash timed out after {config['bash_timeout_seconds']}s"
    except Exception as e:
        return f"ERROR: {e}"


def read_file_contents(path: str, config: dict, logger: logging.Logger) -> str:
    p = Path(path).expanduser()
    logger.info(f"read_file: {p}")
    if not p.exists():
        return f"FILE NOT FOUND: {path}"
    return _truncate(p.read_text(), config["output_truncate_chars"])


def write_file_contents(path: str, content: str, logger: logging.Logger) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    logger.info(f"write_file: {p} ({len(content)} chars)")
    return f"WRITTEN: {path}"


# ── History summarization ─────────────────────────────────────────────────────

def _content_to_text(content: types.Content) -> str:
    """Extract readable text from a Content for summarization input."""
    parts_text = []
    for part in content.parts:
        if hasattr(part, "text") and part.text:
            parts_text.append(part.text[:300])
        elif hasattr(part, "function_call") and part.function_call:
            parts_text.append(f"[tool call: {part.function_call.name}]")
        elif hasattr(part, "function_response") and part.function_response:
            parts_text.append(f"[tool result: {str(part.function_response.response)[:200]}]")
    return " | ".join(parts_text)


def summarize_history(
    client: genai.Client,
    history: list,
    logger: logging.Logger,
) -> list:
    """Compress the first half of history into a summary Content object.

    Bug 1 fix: always returns list[types.Content], never mixes in plain dicts.
    On failure: returns original history unchanged (non-fatal).
    """
    logger.info("Summarizing conversation history (midpoint compression)")
    midpoint = len(history) // 2
    to_summarize = history[:midpoint]

    summary_prompt = (
        "Summarize the following agent conversation history concisely. "
        "Focus on: what was attempted, what succeeded, what failed, current state. "
        "Be factual and brief.\n\n"
        + "\n".join(f"{c.role}: {_content_to_text(c)}" for c in to_summarize)
    )

    try:
        resp = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=summary_prompt,
        )
        summary_text = resp.text
        logger.info(f"Summary ({len(summary_text)} chars): {summary_text[:200]}")
        # Bug 1 fix: return types.Content, not a dict
        summary_content = types.Content(
            role="user",
            parts=[types.Part(text=f"[Progress summary of earlier iterations]\n{summary_text}")],
        )
        return [summary_content] + history[midpoint:]
    except Exception as e:
        logger.warning(f"History summarization failed: {e} — continuing with full history")
        return history


# ── Context loading ───────────────────────────────────────────────────────────

def load_context(task: dict, logger: logging.Logger) -> str:
    """Load context files declared in task['context_keys'] and return as a block.

    Key syntax (namespace:subkey):
      global:machines   → ~/openqueen/context/global/machines.md
      global:logins     → ~/openqueen/context/global/logins.md
      skills:backend    → ~/openqueen/context/skills/backend.md
      project           → <path>/.gemini/project.md + <path>/.gemini/status.md
      project:stack     → <path>/.gemini/stack.md

    Later keys override earlier ones on any conflict (last loaded wins).
    Missing files are warned and skipped — never fatal.
    """
    keys = task.get("context_keys", [])
    if not keys:
        return ""

    project_dir = Path(task["path"]) / ".gemini"
    snippets = []

    for key in keys:
        if key == "project":
            parts = []
            for fname in ("project.md", "status.md"):
                p = project_dir / fname
                if p.exists():
                    parts.append(f"#### {fname}\n{p.read_text().strip()}")
                    logger.info(f"Context loaded: {p}")
                else:
                    logger.debug(f"Context skip (not found): {p}")
            if parts:
                snippets.append(f"### project\n" + "\n\n".join(parts))
        elif ":" in key:
            namespace, _, subkey = key.partition(":")
            if namespace == "project":
                path = project_dir / f"{subkey}.md"
            else:
                path = CONTEXT_DIR / namespace / f"{subkey}.md"
            if path.exists():
                snippets.append(f"### {key}\n{path.read_text().strip()}")
                logger.info(f"Context loaded: {path}")
            else:
                logger.warning(f"Context file not found: {path} — skipping")
        else:
            logger.warning(f"Unknown context key format: '{key}' — skipping (use 'namespace:key')")

    if not snippets:
        return ""

    return (
        "## Loaded Context\n\n"
        "Note: later sections override earlier ones on any conflict.\n\n"
        + "\n\n---\n\n".join(snippets)
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main(task_file: str):
    global _logger

    config = load_config()
    task = parse_task_md(task_file)
    logger = setup_logger(task, config)
    _logger = logger

    logger.info(f"Task: {task['name']}")
    logger.info(f"Project: {task['path']}")
    logger.info(f"Worker: {task['worker']}")

    env_vars = load_env_file(task["env_file"], logger)
    bridge_ok = check_whatsapp_bridge(config, logger)

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.error("GOOGLE_API_KEY not set — add to ~/.zshrc")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    global_prompt = GLOBAL_PROMPT_PATH.read_text()
    context_block = load_context(task, logger)
    if context_block:
        logger.info(f"Context block loaded ({len(context_block)} chars)")
        global_prompt = global_prompt + "\n\n---\n\n" + context_block

    # ── Terminal action helpers ────────────────────────────────────────────

    def do_escalate(reason: str, context: str = "") -> None:
        msg = (
            f"🚨 ESCALATION: {task['name']}\n"
            f"Reason: {reason}\n"
            f"{context}\n"
            f"Log: {config['log_dir']}/{task['name']}-*.log"
        )
        logger.error(f"ESCALATING: {reason}")
        sent = bridge_ok and _send_whatsapp(msg, config, logger)
        if not sent:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            fallback = Path(config["log_dir"]) / f"NOTIFY_FAILED-{task['name']}-{ts}.txt"
            fallback.write_text(msg)
            Path("~/openqueen/ESCALATION_PENDING.txt").expanduser().write_text(msg)
            logger.error(f"Bridge down — escalation written to {fallback}")
        sys.exit(1)

    def do_notify(message: str) -> None:
        msg = f"✅ DONE: {task['name']}\n{message}"
        logger.info(f"DONE: {message}")
        sent = bridge_ok and _send_whatsapp(msg, config, logger)
        if not sent:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            fallback = Path(config["log_dir"]) / f"NOTIFY_FAILED-{task['name']}-{ts}.txt"
            fallback.write_text(msg)
            logger.error(f"Bridge down — result written to {fallback}")

    # ── Tool wrappers ──────────────────────────────────────────────────────

    def tool_run_worker(prompt: str, cwd: str = "") -> str:
        return run_worker(prompt, cwd or task["path"], task, config, env_vars, logger, do_escalate)

    def tool_run_bash(cmd: str, cwd: str = "") -> str:
        return run_bash_cmd(cmd, cwd or task["path"], config, logger)

    def tool_read_file(path: str) -> str:
        return read_file_contents(path, config, logger)

    def tool_write_file(path: str, content: str) -> str:
        return write_file_contents(path, content, logger)

    def tool_notify(message: str) -> str:
        do_notify(message)
        return "DONE"

    def tool_escalate(reason: str, context: str = "") -> str:
        do_escalate(reason, context)
        return "ESCALATED"  # never reached

    tool_map = {
        "run_worker": tool_run_worker,
        "run_bash": tool_run_bash,
        "read_file": tool_read_file,
        "write_file": tool_write_file,
        "notify": tool_notify,
        "escalate": tool_escalate,
    }

    # ── Gemini tool declarations ───────────────────────────────────────────

    worker_name = "Claude" if task["worker"] == "claude" else "Codex"
    tools = types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="run_worker",
            description=f"Run {worker_name} with a specific instruction. Returns worker output.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "prompt": types.Schema(type=types.Type.STRING,
                                          description="Specific instruction for the worker. Be precise."),
                    "cwd": types.Schema(type=types.Type.STRING,
                                       description="Working directory. Defaults to task project path."),
                },
                required=["prompt"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_bash",
            description="Run a shell command to verify state, run tests, check files, git ops.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "cmd": types.Schema(type=types.Type.STRING, description="Shell command."),
                    "cwd": types.Schema(type=types.Type.STRING,
                                       description="Working directory. Defaults to task project path."),
                },
                required=["cmd"],
            ),
        ),
        types.FunctionDeclaration(
            name="read_file",
            description="Read the contents of a file.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="Absolute or ~ path."),
                },
                required=["path"],
            ),
        ),
        types.FunctionDeclaration(
            name="write_file",
            description="Write content to a file (creates parent dirs as needed).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="File path."),
                    "content": types.Schema(type=types.Type.STRING, description="File content."),
                },
                required=["path", "content"],
            ),
        ),
        types.FunctionDeclaration(
            name="notify",
            description="Task complete. Send WhatsApp success notification and exit.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "message": types.Schema(type=types.Type.STRING,
                                           description="Summary using notify format from global_prompt."),
                },
                required=["message"],
            ),
        ),
        types.FunctionDeclaration(
            name="escalate",
            description="Task blocked, cannot continue. Send WhatsApp escalation and exit.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reason": types.Schema(type=types.Type.STRING, description="Why blocked."),
                    "context": types.Schema(type=types.Type.STRING,
                                           description="What was tried and what Federico needs to provide."),
                },
                required=["reason"],
            ),
        ),
    ])

    # ── Main loop ──────────────────────────────────────────────────────────

    max_iter = config["max_iterations"]
    summarize_at = config["history_summarize_at_iteration"]

    # Bug 2 fix: seed history with the initial task message.
    # All subsequent iterations just append to history — no extra "Continue working" message.
    # The function_response turn IS the next user turn; no extra message needed.
    history: list = [
        types.Content(
            role="user",
            parts=[types.Part(text=task["raw"])],
        )
    ]

    for iteration in range(1, max_iter + 1):
        logger.info(f"=== Iteration {iteration}/{max_iter} ===")

        if iteration == summarize_at and len(history) > 4:
            history = summarize_history(client, history, logger)

        system_prompt = global_prompt.replace("{iteration}", str(iteration)).replace(
            "{max_iterations}", str(max_iter)
        )

        try:
            response = client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=history,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[tools],
                    temperature=0.2,
                    http_options=types.HttpOptions(timeout=300_000),  # 5 min per Gemini call
                ),
            )
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            do_escalate(f"Gemini API error: {e}", "Check GOOGLE_API_KEY and quota.")
            return  # do_escalate calls sys.exit; return keeps linters happy

        # Bug 3 fix: append the full model Content (all parts), not just parts[0]
        model_content = response.candidates[0].content
        history.append(model_content)

        # Bug 3 fix: collect ALL function calls across all parts
        function_calls = [
            p.function_call
            for p in model_content.parts
            if hasattr(p, "function_call") and p.function_call
        ]
        text_parts = [
            p.text
            for p in model_content.parts
            if hasattr(p, "text") and p.text
        ]

        if function_calls:
            # Execute all function calls; collect all responses into one user turn
            response_parts = []
            terminal_called = False

            for fc in function_calls:
                fn_name = fc.name
                fn_args = dict(fc.args)
                logger.info(f"Tool call: {fn_name}({list(fn_args.keys())})")

                fn = tool_map.get(fn_name)
                result = fn(**fn_args) if fn else f"ERROR: unknown tool {fn_name}"
                logger.info(f"Tool result ({len(str(result))} chars): {str(result)[:200]}")

                response_parts.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=fn_name,
                        response={"result": result},
                    )
                ))

                if fn_name in ("notify", "escalate"):
                    terminal_called = True
                    break

            # Bug 2 fix: one user Content with all function responses — no extra text message
            history.append(types.Content(role="user", parts=response_parts))

            if terminal_called:
                break

        else:
            # Gemini returned text with no tool calls — treat as done
            final_text = " ".join(text_parts) if text_parts else "Task complete."
            logger.info("Gemini returned text with no tool call — calling notify")
            do_notify(final_text[:500])
            break

    else:
        do_escalate(
            f"Max iterations ({max_iter}) reached without completion",
            f"Task: {task['name']}\nReview the log for full trace.",
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: openqueen <task.md>")
        sys.exit(1)
    main(sys.argv[1])
