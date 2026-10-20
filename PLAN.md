# Sous Flask MVP

Current work is [V2 discovery](#v2-discovery) and its delivery checklist: a private multi-user web app with individual AWS connections. Earlier sections record the implemented V1 iterations. Native iOS and TestFlight are deferred.

## Objective

A local laptop-webcam cooking assistant: persistent recipe state, periodic visual monitoring, automatic step progression, user corrections, one timer, and voice interaction. Recipes are structured data; scrambled eggs is the first bundled/default recipe, not a restriction or tuning target. Codex coordinates Claude Code and Luna and verifies the result.

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
- Load structured recipe JSON from recipes/. Bundle scrambled eggs first (gather; crack/whisk; warm pan/butter; add eggs; stir; plate). Any recipe can have its own ingredients, steps and visual cues. No fixed step count or recipe-specific monitoring prompts. Final completion requires cook confirmation.
- Monitoring speaks on transitions, timer expiry, and actionable issues. Assistive also reads instructions. Deduplicate repeated alerts.
- Controls: Next, Back, Correct step, Pause/Resume, End. Voice: next/back/repeat/pause/resume, set timer for N minutes, cancel timer. Equivalent buttons/text available. Browser speech APIs feature-detected; speech recognition may use an external service.
- One adjustable absolute-deadline timer, independent of recipe advancement; alert once, including after refresh.
- Capture a resized JPEG every 10 seconds when active/visible; Analyze now; at most one request in flight.
- Send current frame, recipe/state, elapsed time, recent observations, and correction. Normalize observed activity, evidence, confidence, completion, optional alert.
- Advance one step after two consecutive valid completion observations at confidence >=0.85; no time-only progression. Reset confirmation streak after correction, pause, provider change, or contradictory observation. Confidence is an initial heuristic, not calibrated probability.
- Revision checks discard late responses; corrections override AI. Obstruction/uncertainty and provider errors never advance; manual actions/timers keep working.
- Four adapters: Ollama /api/chat, OpenAI Responses, Anthropic Messages, AWS Bedrock Converse. Server-side credentials/URLs, configurable model IDs, no automatic fallback.
- SQLite stores sessions/state/revision/deadlines and timestamped actions/corrections/observations with model/latency/errors. No saved frames, raw audio, credentials, or image-bearing requests.
- Refresh restores state; refresh/restart requires Resume. Localhost, one cook/tab, no accounts. No food-safety/fire detection claims, AI-generated free-form recipes, or deployment.

## Interfaces and verification

See [API contract](docs/api.md). Validate inputs, upload limits, provider responses, origins and CSRF tokens; time out remote requests.

Build slices: backend/manual loop -> frontend -> provider monitoring -> review/fixes -> runtime verification. Use unittest/Flask test client with mocked providers. Verify persistence, corrections, bounds, qualifying/uncertain/late observations, timer deduplication, credentials/timeouts/malformed outputs, permission failures, and UI recovery. Live-smoke-test available configured providers; explicitly label others unverified.

User correction: recipes must be data-driven. After P1, delegate moving bundled recipe to JSON, generic validation/loading, session recipe snapshots so config edits don't change an ongoing cook, and tests with a second recipe/different step count. Luna removes eggs-specific UI copy and uses the session snapshot. No recipe editor or AI recipe generation is required for this iteration.

## Docker delivery

After application verification, delegate Dockerfile, compose.yaml and .dockerignore to Claude. Provide `docker compose up --build` with no host Python/Node requirements, persistent named SQLite/secret storage, localhost port 5050 (configurable via SOUS_PORT), and provider variables interpolated from optional .env. Port 5000 is occupied by macOS Control Center on this machine. Do not bake secrets or local data into the image. Use one worker with multiple threads so manual actions remain responsive during inference and in-process observation locks stay valid. Host Ollama uses host.docker.internal with Linux host-gateway mapping. Document container-specific AWS credential configuration; never mount host credentials implicitly. Build/validate/run the container if Docker is available; label unavailable runtime checks explicitly.

## UI redesign

User requested Claude Opus to redesign the interface using UI skills. Replace the warm visual identity with near-white/cool-grey surfaces, charcoal controls and typography, and restrained color for selection and status. Improve setup, current-step hierarchy, camera/timer/recipe organization and completion while preserving all cooking behavior, generic recipes and privacy controls.

Opus owns bounded template/CSS work and necessary UI wiring/test changes, guided by Impeccable and Frontend UI Engineering. Use written guidance without custom executables, respecting execution restrictions. Codex coordinates; Luna independently reviews. Verify setup and cooking at desktop and390px in isolated WebKit under /tmp, run relevant regression checks, record the visual system in docs/design.md, commit the verified milestone and rebuild the local Compose app with persistent storage retained.

## Desk demo recipe

Add an explicitly simulated recipe using four static hand poses, with no ingredients or cooking materials. Load it through the existing JSON recipe mechanism after the default cooking recipe. Instructions explain holding poses for camera samples and using manual controls; final completion remains manual. No gesture-specific engine logic, fake model results or recognition guarantee. Luna owns recipe data and usage documentation; Codex checks loading/API exposure, rebuilds Compose with persistent data retained and commits.

## UI iteration: analysis thinking dots

Work on feature/ui-thinking-dots, leaving main unchanged. Opus implements a small three-dot analysis indicator in the existing grey visual system; Luna reviews. Display meaningful accessible text while the actual observation request is pending, hide decorative dots from assistive technology, respect reduced motion, and clear the indicator on completion, failure or cancellation. Preserve camera privacy and existing state guards. Codex checks delayed-request and cancellation behavior in isolated WebKit, inspects desktop/mobile captures, commits and rebuilds local Compose.

## In-app Bedrock settings

Implement [secure provider settings](docs/settings.md) on this branch. User approved passphrase unlock after server restart. Opus implements the encrypted vault, settings API and explicit Bedrock credential/model discovery integration. Luna implements Settings navigation/forms and simpler cooking setup, then independent review. Codex maintains contracts, verifies mocked AWS and isolated WebKit checks, checks container persistence in the disposable project, commits milestones and rebuilds local Compose. No real AWS credentials are read by agents; live model validation requires user setup.

User also requested snapshot frequency settings:10-second default,15-second maximum. Accept integer seconds1–15, persist this nonsecret preference, use it in the actual camera scheduler and retain single-request-at-a-time behavior. Settings explains shorter intervals can increase cloud usage.

## Conversational voice

Implement [AWS live voice](docs/voice.md) on feature/conversational-voice, created after pulling main. User chose existing AWS credentials and back-and-forth speech with interruption. Opus owns secure duplex backend; Luna owns opt-in audio UI and voice Settings. Codex coordinates, reviews, verifies synthetic tests and Docker, and commits milestones. Preserve unrelated brag-output/. Voice uses server-authoritative recipe context without changing cooking state. Real AWS audio and microphone remain for the user's final trial.

## V2 discovery

Work on feature/v2 from updated main (df712e2). Target: a few trusted users sharing one private instance. User approved the additional features and requires each user to supply their own AWS connection. Chosen path: Web V2 first. TestFlight and native iOS work are deferred because the user has no paid Apple developer account. [V2 specification](docs/v2.md) records the implementation scope and boundaries.

| Module | Responsibility | Depends on |
|---|---|---|
| identity | Username/password accounts, login/logout and account-scoped access | — |
| user-context | Separate cooking sessions, preferences and provider access policy for each user | identity |
| recipe-library | Shared global recipes and private personal recipes; explicit ownership and editing rules | identity |
| recipe-generator | Generate editable recipe drafts and validate them before saving to the personal library | user-context, recipe-library |
| hazard-alerts | Advisory visual concerns, evidence, severity, freshness and acknowledgment | user-context |
| emergency-alerts | Prominent possible-emergency alerts and escalation UX | hazard-alerts |

Suggested build order: identity → user-context and recipe-library → recipe-generator → hazard-alerts → emergency-alerts. Reuse immutable session recipe snapshots. Audit existing singleton session, credential-vault and voice-stream assumptions before enabling multiple simultaneous cooks.

Defaults for implementation: first administrator setup, administrator-created accounts, administrator-managed global recipes, private personal recipes and per-user AWS vaults. One active cook per user; conservative instance-wide voice capacity remains visible. AI alerts are advisory and cannot establish kitchen safety. No automated emergency-service contact is authorized.

Approved additions: personal cooking notes/history, pantry-aware suggestions, serving-size scaling, multiple named timers, pre-cook preparation checks and a monitoring-health display. Household handoff is deferred. Codex coordinates Opus backend and Luna frontend/review in verified slices:

- [x] V2.1: backend accounts, session ownership, per-user settings/vaults and authorization tests (account UI integrates in V2.4).
- [x] V2.2: backend global/personal recipe library, generation, profile inputs and validation tests.
- [x] V2.3: named timers, history/notes, preparation and advisory alert/monitor-health contracts.
- [x] V2.4: responsive account/library/generation/cooking UI and isolated WebKit checks.
- [ ] V2.5: independent isolation/security review, Linux/Compose verification and delivery docs.

## References

- [Proposal](HAI%20Project%20Proposal.md)
- [Claude delegation instructions](CC-Instructions.md)
- [Documentation index](docs/README.md)
- [Agent handoffs](agents/communications.md)
- [Status](STATUS.md)
