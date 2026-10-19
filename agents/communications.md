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
| U1/U2 | Luna | Jinja/JS/CSS interface using agreed API | Completed; six core WebKit scenarios + targeted final-copy checks passed |
| P1 | Claude Code | Four model adapters and monitoring integration | Completed; unit, local live and container live checks passed |
| G1 | Claude Code | Generic JSON recipes and persistent session recipe snapshot | Completed by Opus; 44 tests passed |
| R1 | Luna (fresh review context) | Independent completed-flow review | Completed; actionable findings fixed and re-reviewed |
| V1 | Codex | Inspect, test, run browser, record evidence | Completed; normal app HTTP200, clean new session state |
| D1 | Claude Code | Dockerfile/Compose packaging and container configuration | Completed; build/non-root/restart/live-inference verified |

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

### Available runtime services

Docker Desktop started using its official CLI; Docker server 29.8.2 responds. A network-enabled read-only GET to local Ollama /api/tags confirms qwen3-vl:8b with vision capability already installed. Earlier sandbox loopback probe failed; no models were downloaded. Live local test should use only synthetic fixture images, never user camera imagery. Cloud providers still require configured API credentials/model IDs before live verification.

### Runtime defaults and review notes

Host port 5000 is occupied by macOS Control Center (lsof verified); local and Docker default app URL is 127.0.0.1:5050, configurable SOUS_PORT for Compose. User notes local Ollama is cold; allow ~180s local inference read timeout and show in-flight state. Frontend review notes sent to Luna: forward AbortController signal through post helper, recognize 'resume monitoring', guard same-revision timer state against delayed observation responses. P1 backend fixes remain queued.

### U1 verification

Codex ran WebKit suite after Luna fixed harness failures (macOS temp-path guard, Python function binding, ambiguous permission-error locator, delayed-response assertion timing). Final U1 run: 3 passed + live provider case intentionally skipped in 15.295s; passing cases assert clean browser console. Generated canvas camera and explicit denial mocks only. Desktop/mobile screenshots reviewed; small offscreen skip-link screenshot artifact polish assigned before commit. User confirmed camera may be inaccessible and accepts synthetic-camera testing; real hardware remains manual.

### P1 dispatch

Claude resumes the exact successful B1 session with explicit Opus. Allowed writes: providers.py, app.py, tests/test_app.py, tests/test_providers.py, .env.example, requirements.txt. Four real REST/SDK adapters, strict parsing/sanitized failures and local cold-start allowance; fixes for malformed recipe/provider types and non-ASCII CSRF, optional trusted-host hardening. Luna has stopped writing. Codex owns tests, documentation, and commits.

### R1 UI review result

Fresh Luna reviewer independently inspected U1 and found one P2: enableCamera awaits getUserMedia and only checks that any session is active. A delayed permission result from an ended session can attach to a replacement session. Queue fix for U1 owner after P1 finishes: capture originating session/request generation, invalidate on cleanup, stop stale returned tracks, ensure no stale UI update. Add synthetic delayed-permission browser regression. No other actionable UI findings reported. Root verified the scenario against enableCamera/cleanupSession/createSession code; no real camera access involved.

### Generic recipe clarification

User: do not tune app to scrambled eggs; it should only be the first loaded recipe. G1 will move sample into recipes/*.json, validate/load structured recipe data, retain a session-specific recipe snapshot and exercise another recipe with a different step count. State machine/prompts must consume recipe data only. Luna's next bounded fix removes eggs-specific setup copy and prefers session.recipe snapshot. No recipe editor/AI generation added. P1 is already running; generic behavior will be checked and corrected after its return rather than starting an overlapping writer.

### P1 return

Claude Opus completed four adapters and reviewed input/host protection. Codex observed 32 tests passing. Model prompt is already generic, scoped to selected current step/cue; no eggs-specific model tuning. Local Ollama uses 180s read timeout; cloud adapters use60s with bounded SDK retry. Live qwen smoke running on synthetic frame. Generic JSON recipe/snapshot implementation is next, then Luna's camera-race/snapshot UI fix.

### G1 dispatch / R1 provider review

G1 assigned to Opus with recipes.py, recipes/00-scrambled-eggs.json, app.py, tests/test_recipes.py, tests/test_app.py, and providers.py only if correcting a food-specific prompt. Requires additive migration/backfill and session.recipe response. Frontend changes deferred to Luna after writer finishes. Independent Luna reviews P1 commit6748503 via stable git objects so G1 edits cannot confuse review.

### Live local inference evidence

Opt-in WebKit test passed in51.056s using actual qwen3-vl:8b via Flask/provider adapter with a generated canvas stream. No physical camera/audio or cloud call. Cold timeout/schema worked without recipe-specific tuning or transport adjustments. Cloud adapters remain tested via mocked wire responses only.

### R1 provider review result

No concrete adapter wire/parser defect found. One P2 in observation persistence: after inference the route passes fresh row metadata to record_observation even if step/revision changed. Result belongs to original row/context. Record original step/provider/model/revision, recheck freshness for failure branches too, return409 for stale failed requests rather than502, preserve actual sanitized failure reason in audit record. Queue backend fix and regression after active G1 writer returns. Manual corrections already dominate live state; this fix makes evaluation records accurate.

### G1 return

44 tests observed passing. Recipe JSON loaded per app from RECIPE_DIR (default bundled recipes/), variable step counts supported, snapshot stored and exposed as session.recipe, additive old DB migration preserves session/timer/events. Missing legacy definition yields recipe:null; end/pause/timers remain available. Luna should prefer session.recipe and render missing-definition session with End/timer controls only. Small Sonnet follow-up will fix audit metadata and remove loader's test-only food alias/import-time load, leaving food-specific references in fixtures/data only.

### Audit fix / U2 dispatch

Sonnet completed minimal audit fix and removed loader's test-only food-specific exports/import-time load. Codex ran46 tests successfully; late success/failure provenance checked and in-order failures still502. Luna now sole writer for templates/index.html, static/app.js, tests/test_browser.py and style only as needed: generic setup copy, session snapshot preference, legacy unavailable recipe controls, stale camera-permission lifecycle guard, and generated-only browser regressions for short recipes/snapshot restoration and camera request A resolving during sessionB.

### U2 verification / Docker handoff

Final isolated WebKit run:6 passed+optional live skipped in25.450s. Test harness fixed missing new-session transition, CSS uppercase text assertion, async waits, and Mac WebKit Option+Tab navigation without altering system settings. Generic3-step recipe restores from snapshot after omitted current catalog entry; pending sessionA permission stream stops when sessionB starts. Neutral camera caption and accurate selected-provider still-image copy added. Codex commits UI; Luna stops writing. Sonnet receives bounded Docker packaging task (Dockerfile/compose/.dockerignore/requirements/.env.example/docs/docker.md/tests/test_container.py); app code stable and reviewed.

### D1 initial verification

Docker build and configuration validation succeeded. Test stack sous-verify uses service sous, port5051 and dedicated named volume; uid10001 verified. In-image unit run passed48 cases (46 actual tests,2 opt-in container cases skipped, browser class skipped). Sonnet assigned tiny follow-up: enforce /tmp WebKit and fixed disposable-stack identity in container tests; allow full cold inference to finish during graceful shutdown. Luna's final neutral completion copy is being checked in affected WebKit cases; no further UI writes.

### D1 final verification / delivery

Sonnet enforced dedicated test-project/port identity before mutation, /tmp-only WebKit runtime/profile, and graceful stop210s/Compose220s. Root rebuilt image, ran two container integration tests successfully in29.931s, including real host qwen3-vl and persisted secret/session/recipe/timer across restart. Final fresh Luna review found no packaging defect. Final neutral completion copy passed two affected WebKit cases. Codex stops only sous-verify fixture stack without deleting volume and starts normal app on5050 with separate named storage. No cloud credentials read, no personal browser, no physical camera/mic accessed.

### Delivery

Normal app is running as sous-sous-1 with fresh sous_sous-instance volume, published only127.0.0.1:5050. HTTP200 root page and session:null verified. Docker test project stopped, its volume retained. All requested implementation/review tasks complete; runtime/hardware/cloud limitations documented in docs/validation.md. Codex commits final package/docs locally, no push/deploy.

### U3 redesign dispatch

User explicitly assigns redesign to Claude Opus, overriding the original frontend ownership split. Opus is sole application writer for templates/index.html, static/style.css, optional markup-related static/app.js changes, meaningful browser viewport checks/captures in tests/test_browser.py, and docs/design.md. Direction: clean modern grey surfaces, charcoal hierarchy and restrained state/action color. Read Impeccable and Frontend UI Engineering guidance; do not execute custom skill launchers. Preserve DOM IDs, generic recipes, camera opt-in, stale-result safeguards and all existing cooking behavior. Codex owns root docs, review, isolated WebKit verification, commits and local Compose rebuild. Luna will review stable changes read-only after implementation.

### U3 return and review

Claude Opus completed setup, cooking/tool layout and completion redesign, CSS tokens and display-only JavaScript additions. Backend and provider files unchanged. Codex observed unittest discovery pass (48 reported, optional browser class and two container tests skipped), JavaScript syntax and diff checks pass, and isolated WebKit suite pass (7 executed, optional live inference skipped;25.240s). Fresh Luna static review found no actionable regressions. Viewed setup and cooking desktop/mobile captures. Desktop full-page capture showed sticky column offset after smooth scroll; Luna assigned test-only follow-up to reset scroll instantly, assert top alignment and capture normal viewport plus completion screens before final visual signoff.

### U3 verification and delivery

Luna changed test preparation only: instant scroll, two animation frames, scroll-position and top-alignment assertions, viewport capture and completion captures at explicit desktop/mobile sizes. Targeted setup/cooking checks passed in8.292s; final cooking/completion test after desktop viewport correction passed in6.439s. Root inspected corrected screenshots: step column aligns with workspace, controls and completion fit both widths. Local Compose rebuilt successfully with existing volume retained; HTTP200 and redesigned stylesheet confirmed on5050. No backend/provider changes, personal browser or physical camera access. Design recorded in docs/design.md; root commits verified changes.

### Desk demo dispatch

User requested a recipe that can be tested at a desk without cooking materials. Luna owns only recipes/10-desk-demo.json and docs/recipes.md: four distinct held hand poses, empty ingredients list, explicit demo label, instructions for10-second sampling/two confirmations and final manual finish. No engine changes or fabricated inference. Codex validates loader/API exposure, rebuilds the local container and commits.

### Desk demo return

Luna added four poses and usage instructions, then corrected final-step copy to explicitly require Finish cooking. Codex inspected data/docs, ran eight focused loader/config tests successfully, rebuilt Compose retaining data, and confirmed API lists the default recipe followed by desk-demo with four steps and no ingredients. No UI/engine/provider changes or physical-camera tests. Gesture reliability is not guaranteed; manual Next and final Finish remain available.

### U4 thinking dots dispatch

User requested a new UI branch and Opus/Luna work, starting with analysis thinking dots. Root created feature/ui-thinking-dots from clean main. Opus sole writer for template/CSS/JS and focused browser tests, using Frontend UI Engineering guidance: three subtle decorative dots during actual inference, accessible status text, reduced-motion support and terminal/cancellation cleanup. No backend/model changes. Luna reviews stable diff after implementation; Codex owns docs/tests/commit/local Compose rebuild.

User turned Ollama off during U4. UI checks use synthetic test responses only; defer live inference until the final test session.

### U4 implementation and review

Opus added CSS thinking dots, decorative markup, an analysis visual-state helper and a synthetic pending-request browser test. Initial isolated WebKit suite passed8 executed cases plus1 optional live skip in27.885s. Root inspected desktop/mobile pending captures. Luna review found camera-off hid dots without aborting the pending client request. Luna applied a bounded follow-up in JS/tests: camera-off aborts the request and clears obsolete pending text; pause restores the camera preview caption. Root verifies affected lifecycle cases before commit. No live Ollama requests or personal browser access.

### U4 verification

Root observed3 affected WebKit cases pass in9.637s after Luna's follow-up, including camera-off abort/cleared pending text, reduced motion, pause/end cancellation, delayed permission lifecycle and synthetic response/timer race. JS syntax and diff checks passed. Main remains unchanged; verified implementation stays on feature/ui-thinking-dots. Local Docker update preserves named storage; live inference deferred until user restarts Ollama.
