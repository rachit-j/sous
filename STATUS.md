# Sous project status

## Current

- Phase: conversational AWS voice on feature/conversational-voice, branched from pulled main (0cadc7a).
- Active: Opus backend implemented; 96 reported unit tests passed (5 optional skips). Luna is sole UI writer; independent backend review and disposable Docker build underway. Existing Compose app remains at http://127.0.0.1:5050.
- Blockers: none. Synthetic voice tests only; no physical microphone, personal browser or paid AWS calls by agents.
- Next checkpoint: backend verification/commit, then Luna UI and isolated WebKit/Docker verification. Preserve unrelated brag-output/.

## Milestones

- 2026-10-19: Opus (claude-opus-5-5) completed secure voice backend with explicit CRT HTTP/2/static credentials, same-origin/CSRF startup, bounded queues, seven-minute call limit and lock/session teardown. Codex observed 96 tests passing in13.477s (5 optional skips). No live AWS/audio call. Luna owns browser audio/UI; read-only backend review and Linux image dependency build underway.

- 2026-10-19: Pulled main and created feature/conversational-voice. User chose AWS and back-and-forth voice with interruption. Defined docs/voice.md contract and installed official SDK plus CRT transport. Initial sandbox dependency download hit restricted DNS; authorized dependency installation outside sandbox succeeded. No user credentials accessed or live voice call.

- 2026-10-19: User-driven live Bedrock test succeeded with amazon.nova-lite-v1:0. Read-only live-session inspection confirmed automatic advancement from Open palm to Closed fist, followed by a qualifying fist observation at95% confidence (1 of2 confirmations). This verifies real managed Bedrock inference and automatic progression for the desk demo; real cooking accuracy and audio remain unevaluated. No credentials or camera images were accessed by agents. App work committed; brag-output/ remains owned by the separate in-progress Opus task.

- 2026-10-19: Settings delivery verified. All 14 non-live browser scenarios covered across full/targeted runs; final three affected cases passed in 12.261s after recipe/default refresh, camera-permission harness and repeat-lock fixes. Independent reviewer confirmed listed issues resolved. Two isolated Docker persistence tests passed in 6.571s. Final Linux-image discovery passed 76 reported tests in 4.058s (5 optional skips). Normal Compose rebuilt with data retained; HTTP200, new vault state and default 10-second interval confirmed. Test stack stopped without volume deletion. No real credentials, physical camera, personal browser, live AWS or Ollama used.

- 2026-10-19: Secure Bedrock backend committed e5fb927 after75 reported tests passed in6.582s. Settings UI added by Luna; backend discovery after UI passed76 reported tests (optional5 skips). Initial browser run preserved cooking flows but new settings tests need harness corrections; recipe-selection/model-default refresh defects queued. Real isolated Docker vault restart case passed, including locked state and persisted15-second interval. Older container secret-prefix assertion falsely matched desk-demo and is being corrected.

- 2026-10-19: Created feature/ui-thinking-dots from clean main. Opus added animated analysis dots with accessible status and static reduced-motion rendering. WebKit suite passed8 actual cases with1 live skip in27.885s. Luna found and fixed camera-off request cleanup; three affected lifecycle cases passed in9.637s. Desktop/mobile pending screenshots inspected; no physical camera or live model used.

- 2026-10-19: Added Desk demo — hand gestures through the existing JSON loader, keeping scrambled eggs first. Open palm, fist, thumbs up and OK sign use real camera/model observations; final finish remains manual. Eight focused recipe/config tests passed in0.033s. Docker rebuilt with data retained and API confirmed both recipes, demo has no ingredients and four steps. Physical gesture recognition remains for user testing.

- 2026-10-19: Opus redesign verified with 7 passing isolated WebKit cases (optional live inference skipped), backend discovery green, JavaScript syntax and diff checks clean. Luna static review found no actionable regressions. Setup/cooking/completion desktop and390px captures inspected; instant-scroll test preparation resolved a misleading sticky-panel screenshot. Targeted final cooking/completion test passed in6.439s. Compose rebuilt with existing named storage retained; HTTP200 and new stylesheet confirmed on5050.

- 2026-10-19: User requested clean modern UI with grey accents and splashes of color. Delegated bounded redesign to Claude Opus using Impeccable and Frontend UI Engineering written guidance. No custom skill executables; existing user execution restrictions apply. Codex retains integration, isolated WebKit verification, documentation and commits.

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
- 2026-10-19T20:35Z: Docker Desktop started successfully; Docker server 29.8.2 verified. Existing Ollama service has qwen3-vl:8b with vision support; local live smoke test planned with synthetic test image only.
- 2026-10-19T20:42Z: Isolated WebKit cooking-flow, permission-denial fallback, and synthetic snapshot/timer-race tests passed (3 passed; optional live inference intentionally skipped until P1). Viewed desktop/mobile screenshots; Luna applying a small skip-link capture polish before UI commit. No physical camera or microphone accessed.
- 2026-10-19T20:43Z: Luna finished UI/accessibility polish. Targeted WebKit recheck passed (7.275s); P1 dispatched to Claude explicitly with --model opus, bounded backend/provider scope; no concurrent implementation writer.
- 2026-10-19: Fresh Luna reviewer found one P2 camera-permission race: a permission result requested in an ended session could attach to a replacement session. Fix/test queued for Luna UI after P1 finishes; no real camera was used in tests.
- 2026-10-19: User clarified recipes must be generic; scrambled eggs is only the first loaded recipe. Queued JSON recipe loading/session snapshots and removal of eggs-specific UI copy after active P1 task, before Docker delivery.
- 2026-10-19T20:48Z: Claude Opus completed P1. Codex ran 32 backend/provider tests successfully (optional browser class skipped). Real local qwen3-vl browser smoke test running with generated frame; cloud integrations remain mocked/unverified live.
- 2026-10-19T20:49Z: P1 committed as6748503. G1 dispatched with explicit Opus; independent provider review delegated to Luna using committed code, avoiding moving files.
- 2026-10-19T20:49Z: Real qwen3-vl:8b inference through Flask and isolated WebKit passed in51.056s from cold start, using a generated canvas frame. No physical camera or cloud API used.
- 2026-10-19: Independent provider review found no concrete adapter wire/parser defects. One P2 audit metadata issue queued: late observations/errors must retain their original step/revision context rather than the freshly corrected step; stale failures should return409.
- 2026-10-19T20:52Z: G1 completed by Opus; Codex ran44 tests successfully. JSON recipe loading, different step counts, immutable session recipes and additive legacy database migration covered. Preparing small Sonnet audit-log correction, then Luna snapshot-aware UI/camera-race fix.
- 2026-10-19: G1 committed0025e58. Sonnet dispatched only for small audit regression/test-decoupling follow-up; larger implementation tasks used Opus.
- 2026-10-19T20:54Z: Sonnet audit fix complete and46 tests passed. Stale success/failure audit rows retain request context; stale failures return409. Generic loader has no food-specific alias/import-time default load. Luna authorized final UI snapshot/camera fix as sole writer.
- 2026-10-19T21:18Z: U2 WebKit suite green:6 passed+optional live skipped in25.450s. Covered custom3-step recipe/snapshot after catalog removal and camera permission A resolving during sessionB (stale track stopped). Keyboard accessibility check uses macOS WebKit Option+Tab; no system settings changed. Docker packaging next.
- 2026-10-19T21:20Z: Dockerfile/Compose written and reviewed for runtime allowlist, non-root user, one multithreaded worker, named storage, localhost port and host Ollama routing. Compose configuration validation passed; image build started while Claude finishes focused container tests/docs.
- 2026-10-19T21:22Z: Image build passed; dedicated sous-verify stack started on5051 and uid10001 confirmed. Linux-image unittest discovery passed48 cases (46 executed,2 optional container cases skipped; browser class also skipped). Final Docker test isolation/shutdown polish delegated; restart/live model checks next.
- 2026-10-19T21:24Z: Both real container integration tests passed in29.931s: generated-frame container->host Ollama and persisted session/recipe/timer/signing secret across restart. Final packaging review found no actionable defects. Affected recipe-neutral completion flows passed two targeted WebKit cases in9.893s. Test stack stopping without volume deletion; normal localhost5050 stack starting with separate data.
- 2026-10-19T21:25Z: Test fixture stack stopped with volume retained. Normal sous-sous-1 container started with separate fresh named volume, only127.0.0.1:5050 published. Root page HTTP200 and active-session API returnednull. Final packaging/documentation milestone ready to commit; no pushes/deployments.
