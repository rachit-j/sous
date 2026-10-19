# Agent instructions

## Shared context

Read [PLAN.md](PLAN.md), [STATUS.md](STATUS.md), [docs/README.md](docs/README.md), and [agents/communications.md](agents/communications.md). Preserve the user's proposal and existing files.

Read CC-Instructions.md before delegating to Claude Code.

## Responsibilities

- Codex organizes work, owns integration and final verification, and maintains PLAN.md, STATUS.md, and agents/communications.md.
- After setup Codex remains the orchestrator: delegate application implementation/fixes; personally inspect, verify, coordinate, document and commit milestones.
- Claude Code implements bounded backend/provider tasks using the workflow in CC-Instructions.md.
- Use Claude Opus explicitly for substantial implementation, reasoning, and review; Sonnet may handle straightforward fixes at Codex's discretion. Keep configured effort unless the user specifies it.
- Luna (gpt-6-luna) implements the interface and performs bounded reviews.
- Assign explicit goals, context, allowed files, constraints, mode, acceptance checks, and expected return. Agents may not recursively delegate or initiate automatic review loops.
- Only one implementation agent writes in this checkout at a time. Other agents may inspect stable files read-only. Wait for the writer to finish before changing its files.
- Report results and blockers to Codex; Codex records durable handoffs. Never claim checks ran without observed output.

## Execution limits

- No arbitrary code execution, generated utility scripts, system code, or custom binaries.
- Never use the user's Chrome, personal browsers, profiles, tabs or sessions for testing. Use only an isolated WebKit testing browser installed under /tmp with a temporary profile. Browser automation must be app tests, not unrelated system automation.
- Ordinary file inspection/editing, approved Claude CLI delegation, dependency setup, app execution, and meaningful tests are allowed. Do not execute application code for unrelated automation.
- Claude tools: Read, Glob, Grep for review; add Edit, Write for implementation. Codex runs commands/tests. Never bypass permissions.
- Use the find-skills skill when a specific workflow need calls for skill discovery. Inspect available skills first; do not silently install tools or skills.
- Codex is authorized to commit frequently after inspecting and verifying milestones. Delegated agents must not commit. No pushes, publishing, deployments, contacting others, or destructive Git recovery without explicit authorization.
- Do not read/print credentials. Keep secrets, frames, raw audio, SQLite files, CLI session artifacts, and environment files out of Git.
- User-authorized in-app Bedrock credentials use a passphrase-encrypted vault; unlock after server restart. Never persist the unlock key/passphrase beside ciphertext, echo stored secrets to UI, or modify process environment to apply saved credentials. Use synthetic credentials in tests. Snapshot interval is configurable from1–15 seconds, default10.

## Implementation quality

- Flask/Jinja/plain JS/CSS/SQLite; minimize dependencies and abstractions.
- Recipes are data-driven, loaded from JSON. Scrambled eggs is only the first bundled/default example. Never hardcode its name, step count or cooking cues into UI copy, state logic or model prompts; use selected recipe data. Preserve a recipe snapshot for each session.
- Validate external inputs and model outputs; preserve manual controls on model failure.
- Model confidence is heuristic. Camera observations cannot verify food safety.
- Test state transitions, stale observations, timers, provider mapping/failure, and persistence. Browser-check actual UI behavior.
- Final delivery includes Dockerfile/Compose, persistent named storage, optional provider env configuration and no host language dependency requirements. No implicit host-credential mounts. Codex verifies Compose/build/run when a Docker daemon is available.
- Update linked Markdown docs when behavior or interfaces change. Use stable filenames/headings and relative links for later graphing.
