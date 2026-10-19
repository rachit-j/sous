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
| B1 | Claude Code | Flask/SQLite recipe loop, actions, tests and dependency configuration | Completed; 16 tests verified by Codex |
| U1 | Luna | Jinja/JS/CSS interface using agreed API | Implementing as sole writer, including WebKit tests |
| P1 | Claude Code | Four model adapters and monitoring integration | Waiting for B1/U1 |
| R1 | Luna | Independent completed-flow review | Waiting for implementation |
| V1 | Codex | Inspect, test, run browser, record evidence | Ongoing |
| D1 | Claude Code | Dockerfile/Compose packaging and container configuration | After application verification |

## Handoffs

### Setup

Codex recorded initial repository state and read Claude instructions. Claude CLI is installed; initial auth check failed. User reports login completed. Backend owns app.py, recipes.py, requirements.txt, .env.example, .gitignore and tests/. Frontend owns templates/ and static/. Codex owns shared documentation. No simultaneous implementation writers.

### B1/U1 dispatch

Existing subscription authentication verified outside sandbox. Luna is reviewing docs/api.md read-only. Claude will implement the backend/manual loop and test contract using file tools only; Codex runs tests. Provider network analysis is P1, after UI handoff. Both must preserve all user/shared docs.

### Model preference

User requested mostly Claude Opus, with Sonnet for easy tasks at Codex's judgment. B1 was already running with the configured default; subsequent substantive calls must pass --model opus. Luna remains gpt-6-luna as requested.

### U1 review and setup evidence

Luna reviewed the contract read-only and found no blockers. Backend and UI shape remain docs/api.md. Codex created a Python 3.12 venv and installed Flask/python-dotenv/requests/boto3 using uv; sandbox PyPI DNS failed, external network execution succeeded. After setup, Codex only coordinates, reviews, tests, documents and commits; application fixes are delegated.

### Browser testing restriction

User requires a dedicated WebKit browser under /tmp, never their Chrome or personal browsers/profiles. Codex will use Playwright WebKit with an isolated temporary profile for application tests. No personal browser was used for app testing. Browser surface discovery only was performed before this instruction; no tabs opened or interacted with.

### Docker request

Final deliverable must run with Docker Compose, retain SQLite/secret state in a named volume, keep secrets out of image, and work with host Ollama across macOS/Linux via host.docker.internal. One multithreaded worker; no implicit host credential mounting. Codex checks available Docker tooling and delegates packaging after app checks.

### B1 return / U1 implementation handoff

Claude completed backend with configured default confirmed Opus. Codex observed 16 passing backend tests. create_app(test_config) is the app entrypoint; index injects csrf_token; times are Unix seconds; session ID integer. Backend has full observation state/concurrency route but providers.py is absent until P1. Luna may write templates/index.html, static/app.js, static/style.css, tests/test_browser.py, requirements-dev.txt only. Browser tests gated SOUS_BROWSER_TESTS=1, WebKit under /tmp, no personal browsers. Backend review notes queued for P1: validate recipe/provider types before dict membership; non-ASCII CSRF header should yield 403 instead of TypeError. No overlapping implementation writers.
