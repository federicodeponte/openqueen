# Task: t5-rocketlist-gemini-ctx

## Project
path: ~/rocketlist-minimal
worker: claude
context:
  - global:machines
  - global:logins

## Objective
Create the file ~/rocketlist-minimal/.gemini/project.md — a concise project brief
that future Gemini orchestration tasks can load as context.

Read these files first to gather the facts:
- ~/rocketlist-minimal/CLAUDE.md
- ~/rocketlist-minimal/README.md

Then write .gemini/project.md with the following sections:
- What the product does (1-2 sentences)
- Stack (frontend, backend, database, hosting)
- Branching model and deployment workflow
- Key URLs (prod, preview, API)
- AX41 backend service name and how to restart it
- Supabase project ID
- GitHub account to use

Keep it under 60 lines. Factual only — no fluff.

## Done When
- ~/rocketlist-minimal/.gemini/project.md exists
- File contains the word 'rocketlist-minimal' (correct path)
- File is under 80 lines
