# Task: t8-codex-worker

## Project
path: ~/rocketlist-minimal
worker: codex
context:
  - global:machines
  - skills:backend

## Objective
Add a GET /status endpoint to the Rocketlist FastAPI backend (backend/api.py).

The endpoint must:
- Require no auth (public)
- Return JSON: {"status": "operational", "version": "1.0.0"}
- Follow the existing FastAPI patterns (look at /health and /ping for reference)

Add a test in backend/tests/test_api.py following existing patterns.

Run: cd ~/rocketlist-minimal && python3 -m pytest backend/tests/test_api.py -q 2>&1 | tail -5

Commit all changes.

## Done When
- grep -q '/status' ~/rocketlist-minimal/backend/api.py
- cd ~/rocketlist-minimal && python3 -m pytest backend/tests/test_api.py -q 2>&1 | tail -3 shows all passed
- cd ~/rocketlist-minimal && git log --oneline -1 shows new commit mentioning status
