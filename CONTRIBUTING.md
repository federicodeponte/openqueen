# Contributing to OpenQueen

Thanks for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/buildingopen/openqueen
cd openqueen
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

## Running Tests

```bash
pytest tests/ -v               # all tests
pytest tests/test_compiler.py  # specific module
```

No API keys required — all Gemini calls are mocked.

## Project Structure

```
openqueen/
├── agent.py           Worker: Gemini drives Claude/Codex
├── dispatch.py        Task runner: parallel projects, queue
├── listen.py          Transport listener (Telegram / WhatsApp)
├── monitor.py         Watchdog: timeout, Done When checks
├── lib/
│   └── compiler.py    NL → task.md compiler (Gemini)
├── wa-listener/       Node.js WhatsApp bridge (Baileys)
├── tests/             pytest suite (64 tests, no API keys)
└── install.sh         curl | bash installer
```

## Adding a Transport

Implement the listener in `listen.py` (see Telegram/WhatsApp sections).
The listener sends tasks to `dispatch.py` via `QUEUE.json`.

## Submitting Changes

1. Fork the repo
2. Create a branch: `git checkout -b feat/my-feature`
3. Make changes + add tests
4. Run `pytest tests/ -v` — all must pass
5. Open a PR with a clear description

## Code Style

- Python 3.10+, no external formatters required
- Keep functions small and focused
- No hardcoded paths — use `OQ_HOME` from env
- Tests must not require API keys

## Issues

Found a bug? Please open an issue with:
- Your transport (Telegram/WhatsApp)
- Python version (`python3 --version`)
- Error message / log snippet
