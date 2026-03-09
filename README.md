# OpenQueen

**Autonomous coding agent controlled by WhatsApp or Telegram.**

Send a message. Gemini 3.1 Pro compiles it into a task, drives Claude/Codex to completion, and notifies you when done — all while you're away from your desk.

```
You: "add dark mode to the dashboard"
  └─► Gemini compiles task → Claude executes → "Done! 3 files changed" ✓
```

## Architecture

```
WhatsApp / Telegram
       │
   listen.py          ← watches for !task messages
       │
  dispatch.py         ← parallel task runner (same project queues)
       │
  lib/compiler.py     ← Gemini 3.1 Pro compiles NL → task.md
       │
   agent.py           ← Gemini orchestrates Claude/Codex worker
       │
  monitor.py          ← watchdog, timeout, Done When checks
       │
   notify             ← send result back to your phone
```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/federicodeponte/openqueen/main/install.sh | bash
```

Then configure:

```bash
openqueen init
```

**Requirements:** Python 3.10+, git. Node.js 18+ for WhatsApp transport.

## Quick Start

1. **Install** (30 seconds):
   ```bash
   curl -fsSL .../install.sh | bash
   ```

2. **Configure** (2 minutes):
   ```bash
   openqueen init
   # → enter Gemini API key
   # → connect WhatsApp or Telegram
   ```

3. **Start**:
   ```bash
   systemctl enable --now openqueen
   ```

4. **Use** — send a message from your phone:
   ```
   fix the auth bug in my-api
   add tests for the payment module
   refactor the dashboard to use TypeScript
   ```

## Transport Options

| Transport | Setup | Risk |
|-----------|-------|------|
| **WhatsApp** (primary) | Scan QR code on first run | Personal use — unofficial API |
| **Telegram** *(experimental)* | Create bot via @BotFather | None — official API |

## Configuration

Edit `~/openqueen/.env`:

```env
OPENQUEEN_HOME=~/openqueen
GOOGLE_API_KEY=your_gemini_key

# Transport
OQ_TRANSPORT=whatsapp          # or: telegram (experimental)
OQ_WORKER=claude               # or: codex, gemini

# Telegram
OQ_TELEGRAM_TOKEN=your_bot_token
OQ_TELEGRAM_CHAT_ID=your_chat_id

# WhatsApp
# OQ_GROUP_JID=1234567890-1234@g.us

# Auto-scan workspace for projects (optional)
# OQ_WORKSPACE=~/projects
```

Edit `~/openqueen/config.json` for advanced settings:

```json
{
  "max_iterations": 20,
  "timeout_minutes": 30,
  "history_max_chars": 60000,
  "worker": "claude"
}
```

## Task Files

Tasks are compiled automatically from natural language. You can also write them manually:

```markdown
# Task: fix-auth-bug

## Project
path: ~/my-project
worker: claude
max_iterations: 15

## Objective
Fix the null pointer exception in auth.py line 42.
The bug triggers when user.email is None.

## Done When
- test -f ~/my-project/auth.py
- python3 -m pytest ~/my-project/tests/test_auth.py -q
```

Send the file path or drop it in `~/openqueen/tasks/`.

## Projects

Define your projects in `~/openqueen/projects.json`:

```json
[
  {"name": "my-api", "path": "~/my-api", "description": "REST API backend"},
  {"name": "frontend", "path": "~/frontend", "description": "React dashboard"}
]
```

Or set `OQ_WORKSPACE=~/projects` to auto-scan for git repositories.

## Commands

```bash
openqueen init        # Setup wizard
openqueen status      # Show running tasks
openqueen logs        # Tail session log
openqueen run <task>  # Run a task.md file directly
openqueen version     # Show version
```

## Docker (optional)

```bash
cp .env.example .env  # fill in your keys
docker compose up -d
```

## Development

```bash
git clone https://github.com/federicodeponte/openqueen
cd openqueen
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

## License

MIT
