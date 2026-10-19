# Sous Flask MVP

## Objective

A local laptop-webcam assistant for one scrambled-eggs cooking session: persistent recipe state, periodic visual monitoring, automatic step progression, user corrections, one timer, and voice interaction. Codex coordinates Claude Code and Luna and verifies the result.

## Workflow and documentation

- Establish root AGENTS.md, this plan, STATUS.md, docs/README.md, and agents/communications.md before implementation.
- Codex maintains current phase, assignments, blockers, next checkpoint, and timestamped milestones in STATUS.md; records handoffs and resolutions in agents/communications.md.
- Keep documentation graph-friendly with stable headings, filenames, relative links, and evidence references. Never store private CLI artifacts in Git.
- Follow CC-Instructions.md: preflight authentication, bounded file-tool delegation, private temporary briefs/results, exact-session resumption, no recursive agents, no bypass permissions. Inspect results and changes; Codex runs checks.
- Select Claude Opus for substantive tasks, Sonnet only for simple follow-ups. B1 began on the configured default before this preference arrived.
- Browser testing uses an isolated Playwright WebKit installation/profile under /tmp only. Never use personal Chrome or other personal browsers/sessions.
- Delegate backend/state and provider integration to Claude Code, frontend to Luna, then independent review. One writer at a time; read-only review may overlap stable inputs.
- No arbitrary code execution, generated utility/system scripts, or custom binaries. Ordinary inspection/editing, authorized Claude CLI, dependency setup, app execution, and tests only. Codex commits verified milestones frequently; no pushes/deployment.
- Use find-skills when a concrete workflow need calls for discovery; no silent installation.

## Application

- Flask, Jinja, plain JS/CSS, SQLite. No frontend build, task queue, or agent framework.
- Choose configured provider/model and Monitoring or Assistive mode before starting. Show preview, current step, progress, timer, recent observations/evidence, confidence, and time.
- Recipe: gather ingredients; crack/whisk; warm pan/add butter; add eggs; gently stir; transfer to plate. Final completion requires cook confirmation.
- Monitoring speaks on transitions, timer expiry, and actionable issues. Assistive also reads instructions. Deduplicate repeated alerts.
- Controls: Next, Back, Correct step, Pause/Resume, End. Voice: next/back/repeat/pause/resume, set timer for N minutes, cancel timer. Equivalent buttons/text available. Browser speech APIs feature-detected; speech recognition may use an external service.
- One adjustable absolute-deadline timer, independent of recipe advancement; alert once, including after refresh.
- Capture a resized JPEG every 10 seconds when active/visible; Analyze now; at most one request in flight.
- Send current frame, recipe/state, elapsed time, recent observations, and correction. Normalize observed activity, evidence, confidence, completion, optional alert.
- Advance one step after two consecutive valid completion observations at confidence >=0.85; no time-only progression. Reset confirmation streak after correction, pause, provider change, or contradictory observation. Confidence is an initial heuristic, not calibrated probability.
- Revision checks discard late responses; corrections override AI. Obstruction/uncertainty and provider errors never advance; manual actions/timers keep working.
- Four adapters: Ollama /api/chat, OpenAI Responses, Anthropic Messages, AWS Bedrock Converse. Server-side credentials/URLs, configurable model IDs, no automatic fallback.
- SQLite stores sessions/state/revision/deadlines and timestamped actions/corrections/observations with model/latency/errors. No saved frames, raw audio, credentials, or image-bearing requests.
- Refresh restores state; refresh/restart requires Resume. Localhost, one cook/tab, no accounts. No food-safety/fire detection claims, free-form recipes, or deployment.

## Interfaces and verification

See [API contract](docs/api.md). Validate inputs, upload limits, provider responses, origins and CSRF tokens; time out remote requests.

Build slices: backend/manual loop -> frontend -> provider monitoring -> review/fixes -> runtime verification. Use unittest/Flask test client with mocked providers. Verify persistence, corrections, bounds, qualifying/uncertain/late observations, timer deduplication, credentials/timeouts/malformed outputs, permission failures, and UI recovery. Live-smoke-test available configured providers; explicitly label others unverified.

## Docker delivery

After application verification, delegate Dockerfile, compose.yaml and .dockerignore to Claude. Provide `docker compose up --build` with no host Python/Node requirements, persistent named SQLite/secret storage, localhost port exposure, and provider variables interpolated from optional .env. Do not bake secrets or local data into the image. Use one worker with multiple threads so manual actions remain responsive during inference and in-process observation locks stay valid. Host Ollama uses host.docker.internal with Linux host-gateway mapping. Document container-specific AWS credential configuration; never mount host credentials implicitly. Build/validate/run the container if Docker is available; label unavailable runtime checks explicitly.

## References

- [Proposal](HAI%20Project%20Proposal.md)
- [Claude delegation instructions](CC-Instructions.md)
- [Documentation index](docs/README.md)
- [Agent handoffs](agents/communications.md)
- [Status](STATUS.md)
