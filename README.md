# openqueen

Gemini 3.1 Pro orchestrates Claude/Codex workers autonomously on AX41.
You write a task file. Gemini drives the worker until done, then notifies you on WhatsApp.

## Install (AX41)

```bash
pip3 install google-genai python-dotenv pytest --break-system-packages
echo 'export GOOGLE_API_KEY=...' >> ~/.zshrc && source ~/.zshrc
ln -sf ~/openqueen/agent.py /usr/local/bin/openqueen
chmod +x ~/openqueen/agent.py
```

## Usage

```bash
openqueen ~/openqueen/tasks/my-task.md

# Tail the log while it runs
tail -f ~/openqueen/logs/my-task-*.log
```

## task.md format

```markdown
# Task: short-slug-name

## Project
path: ~/path/to/project        # required
worker: claude                 # claude | codex (default: claude)
new_project: false             # true = Claude creates the dir + git init
env_file: ~/project/.env.local # optional — vars injected into worker env

## Objective
What needs to be done. Be specific.

## Context / Constraints
Anything Gemini and the worker need to know.

## Done When
- Specific verifiable criterion 1
- Specific verifiable criterion 2
```

## Config (`config.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `max_iterations` | 10 | Hard cap on Gemini iterations |
| `max_retries_on_failure` | 3 | Same error N times → escalate |
| `worker_timeout_seconds` | 300 | Per claude/codex call timeout |
| `bash_timeout_seconds` | 60 | Per run_bash call timeout |
| `output_truncate_chars` | 6000 | Max chars returned to Gemini per call |
| `history_summarize_at_iteration` | 5 | Compress history at this iteration |
| `whatsapp_group` | test group | WhatsApp group ID for notifications |
| `whatsapp_bridge` | `~/queen/whatsapp_bridge.py` | Bridge script path |
| `log_dir` | `~/openqueen/logs` | Log directory |

## Escalation

Gemini escalates (WhatsApp message + exit 1) on:
- Missing credentials or access
- Git conflicts requiring human decision
- Same error 3 times with no change
- Max iterations reached

If the WhatsApp bridge (clawdbot) is down, escalation is written to:
- `~/openqueen/logs/NOTIFY_FAILED-<task>-<date>.txt`
- `~/openqueen/ESCALATION_PENDING.txt`

## Tests

```bash
cd ~/openqueen
python3 -m pytest tests/ -v
```

## Architecture

```
openqueen task.md
      ↓
Gemini 3.1 Pro (orchestrator)
      ↓ tool calls
run_worker / run_bash / read_file / write_file / notify / escalate
      ↓
claude -p --permission-mode dontAsk   (research, generic, new projects)
codex exec - --dangerously-bypass-... (coding, bug fixes, tests)
      ↓
WhatsApp notification on done or blocked
```
