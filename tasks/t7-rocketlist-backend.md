# Task: t7-rocketlist-backend

## Project
path: ~/rocketlist-minimal
worker: claude
context:
  - global:machines
  - global:logins
  - skills:backend
  - project

## Objective
Add a GET /version endpoint to the Rocketlist FastAPI backend (backend/api.py).

The endpoint must:
- Require no auth (public, no API key)
- Return JSON: {"version": "1.0.0", "service": "rocketlist-api"}
- Follow the existing FastAPI patterns in the file (look at /health for reference)

Then add a test for it in backend/tests/test_api.py — follow the existing test patterns.

Run the tests to verify: cd ~/rocketlist-minimal && python3 -m pytest backend/tests/test_api.py -q 2>&1 | tail -20

Commit all changes.

## Done When
- grep -q 'GET /version\|/version' ~/rocketlist-minimal/backend/api.py
- cd ~/rocketlist-minimal && python3 -m pytest backend/tests/test_api.py -q 2>&1 | tail -5 shows passing
- cd ~/rocketlist-minimal && git log --oneline -1 shows a new commit
