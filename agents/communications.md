# Agent communication log

Coordinator: Codex. Agents return findings through their task channel; Codex records shared decisions here. One implementation writer at a time.

## Shared decisions

- Read [AGENTS.md](../AGENTS.md), [PLAN.md](../PLAN.md), and [API contract](../docs/api.md).
- All observations use server-side validation and optimistic revision checks. Manual correction wins.
- Credentials remain on server. Do not store images/audio. No arbitrary scripts/custom binaries.
- User files are preserved. Codex commits verified milestones frequently (user authorized); subagents do not commit. No external publication.

## Task ledger

| ID | Owner | Assignment | State |
|---|---|---|---|
| B1 | Claude Code | Flask/SQLite recipe loop, actions, tests and dependency configuration | Dispatched |
| U1 | Luna | Jinja/JS/CSS interface using agreed API | Read-only contract review; writes wait for B1 |
| P1 | Claude Code | Four model adapters and monitoring integration | Waiting for B1/U1 |
| R1 | Luna | Independent completed-flow review | Waiting for implementation |
| V1 | Codex | Inspect, test, run browser, record evidence | Ongoing |

## Handoffs

### Setup

Codex recorded initial repository state and read Claude instructions. Claude CLI is installed; initial auth check failed. User reports login completed. Backend owns app.py, recipes.py, requirements.txt, .env.example, .gitignore and tests/. Frontend owns templates/ and static/. Codex owns shared documentation. No simultaneous implementation writers.

### B1/U1 dispatch

Existing subscription authentication verified outside sandbox. Luna is reviewing docs/api.md read-only. Claude will implement the backend/manual loop and test contract using file tools only; Codex runs tests. Provider network analysis is P1, after UI handoff. Both must preserve all user/shared docs.

### Model preference

User requested mostly Claude Opus, with Sonnet for easy tasks at Codex's judgment. B1 was already running with the configured default; subsequent substantive calls must pass --model opus. Luna remains gpt-6-luna as requested.

### U1 review and setup evidence

Luna reviewed the contract read-only and found no blockers. Backend and UI shape remain docs/api.md. Codex created a Python 3.12 venv and installed Flask/python-dotenv/requests/boto3 using uv; sandbox PyPI DNS failed, external network execution succeeded. After setup, Codex only coordinates, reviews, tests, documents and commits; application fixes are delegated.
