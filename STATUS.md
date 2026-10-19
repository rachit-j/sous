# Sous project status

## Current

- Phase: frontend implementation.
- Active: Luna building U1 interface and isolated WebKit tests; Codex reviewing backend and coordinating.
- Blockers: Docker CLI/Compose available but daemon not running; packaging runtime checks will need it started. No configured live model yet.
- Next checkpoint: verify/commit backend, test Luna's UI, then delegate P1 provider integration to Claude Opus.

## Milestones

- 2026-10-19: Read proposal and CC-Instructions.md; plan approved for implementation.
- 2026-10-19: Initial Git state has only user-owned untracked CC-Instructions.md and HAI Project Proposal.md. No existing application.
- 2026-10-19: Claude CLI 2.1.303 available; initial preflight reported unauthenticated. User completed login.
- 2026-10-19: Established shared instructions, plan, status, documentation index, API contract, and agent communication log.
- 2026-10-19T20:27Z: Verified Claude authenticated via existing subscription outside sandbox. Delegated Luna's read-only interface contract review.
- 2026-10-19: User authorized frequent commits. Codex will commit verified milestones; no pushes or deployments.
- 2026-10-19T20:29Z: Luna completed read-only contract review with no blocking gaps. Created local Python 3.12 environment and installed planned dependencies; PyPI required network access outside sandbox and succeeded.
- 2026-10-19T20:29Z: Setup documentation is ready; Codex now acts as orchestrator, delegating application code and fixes while maintaining docs, verification and commits.
- 2026-10-19: User restricted browser testing to isolated WebKit under /tmp; no personal Chrome/browser sessions. Preparing this test environment.
- 2026-10-19T20:32Z: Installed official Playwright package in project venv and WebKit 27.2 runtime under /private/tmp/sous-webkit. No personal browser opened. Claude has written backend/recipe/config files and is completing tests; Luna remains read-only pending handoff.
- 2026-10-19: User requested final Docker Compose packaging for cross-platform dependency isolation; added as final delivery milestone.
- 2026-10-19T20:34Z: Claude completed B1 using claude-opus-5-5 (confirmed returned usage). Codex ran all 16 actual backend tests successfully; Claude's reported count of 18 was corrected. B1 includes observation hook/state safeguards, real adapters pending P1. Luna authorized U1 as sole implementation writer.
