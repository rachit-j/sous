# Validation evidence

## Current state

MVP application and Docker packaging verified. Recipes are generic structured JSON, with scrambled eggs bundled first. Physical camera/audio and live cloud providers remain manual/configuration-dependent checks.

## Observed checks

- 2026-10-19 thinking dots: isolated WebKit suite passed8 actual cases plus1 optional live-model skip in27.885s. New delayed synthetic observation test checks pending dots/busy state, success, failure, previous-observation retention, pause/end cleanup and reduced motion. After Luna's camera-off cancellation fix,3 affected lifecycle tests passed in9.637s, including aborted request/dots/status cleanup on camera-off and existing permission/timer races. Desktop/mobile pending screenshots inspected. JavaScript syntax and diff checks passed. Ollama intentionally off; no live inference attempted.

- 2026-10-19 desk demo:8 focused recipe-loader/config tests passed in0.033s; final default-order loader recheck passed. Running Docker API lists desk-demo with empty ingredients and4 steps after the default recipe. Container rebuilt with persistent data retained. This validates recipe delivery, not physical hand-gesture recognition; no camera accessed.

- 2026-10-19 UI redesign: Claude Opus implemented grey/charcoal surfaces and restrained state colors using Impeccable and Frontend UI Engineering guidance. Unittest discovery passed48 reported cases in0.450s (46 unit/provider/recipe cases executed; optional browser class and two container cases skipped). Isolated WebKit suite passed7 cases with1 optional live-model skip in25.240s. Added setup preview checks, horizontal overflow checks and correction-form clipping checks at1280px/390px. Instant-scroll preparation plus explicit step/workspace alignment fixed a misleading capture during smooth scrolling. Final affected cooking/completion test passed in6.439s; setup/cooking/completion desktop/mobile screenshots inspected. JavaScript syntax and diff whitespace checks passed. Independent Luna static review found no actionable regressions. Local Compose rebuild succeeded with named storage retained; HTTP200 and new CSS served on5050. Provider inference was not repeated for this visual change.

- 2026-10-19: `.venv/bin/python -m unittest discover -s tests -v` — 16 tests passed (0.214 s final B1 run). Coverage includes state persistence, navigation/corrections, timers, CSRF/origin protection, observation threshold/stale-result/busy handling with mocked provider module.
- Docker CLI and Compose are installed; initial `docker info` could not connect because its daemon socket does not exist. Container runtime verification pending.
- Docker Desktop was started through its official CLI; Docker server 29.8.2 now responds. Container build/run still pending packaging.
- Local Ollama `/api/tags` confirms an existing vision-capable `qwen3-vl:8b`; live synthetic-image analysis pending provider adapter.
- 2026-10-19: `TMPDIR=/private/tmp SOUS_BROWSER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=/private/tmp/sous-webkit .venv/bin/python -m unittest tests.test_browser -v` — 3 passed, 1 optional live-inference case skipped, 15.295s. Verified manual next/back/correction, timer command/expiry, reload/recovery, completion/new/end, simulated denied camera permission, and generated video snapshot through the API with a delayed-response/timer acknowledgement race. No page errors or unexpected console errors in passing cases.
- Viewed desktop/mobile screenshots under `/private/tmp/sous-webkit-artifacts`. No physical webcam/microphone or personal browser accessed. Real hardware framing and audible speech remain manual checks.
- After accessibility/screenshot polish, reran the manual cooking scenario including keyboard skip-link behavior: passed in 7.275s. Screenshot capture resets focus/scroll; unfocused skip link remains clipped.
- 2026-10-19: After P1, full unittest discovery — 32 tests passed in 0.854s, optional browser class skipped. Four provider wire formats, strict parsing, sanitized transport/refusal failures, trusted hosts, malformed session inputs and non-ASCII CSRF regressions covered. Cloud providers have not been live-tested.
- 2026-10-19: Opt-in real local Ollama WebKit test — passed in51.056s, including cold `qwen3-vl:8b` startup, generated-canvas video capture, real Flask/provider request, validated observation and visible confidence. This verifies transport/integration, not cooking accuracy. No physical camera or external provider involved.
- 2026-10-19: G1 unittest discovery —44 tests passed in0.509s, optional browser class skipped. Added tests for generic recipe files/order/validation, 1- and2-step recipes, observed/manual transitions against their cues, snapshots after definition changes/removal, and additive migration of prior session/timer/event data.
- 2026-10-19: Audit follow-up unittest discovery —46 tests passed in0.425s, optional browser class skipped. Late success/failure rows retain original request step/revision, sanitized stale failures return409, and corrected/paused session state remains unchanged.
- 2026-10-19: Final U2 WebKit suite —6 passed+1 optional live test skipped in25.450s. Includes custom3-step recipe, snapshot recovery after catalog removal, stale permission result across session replacement, synthetic observation/timer race, manual cooking/recovery, denied camera fallback and keyboard skip-link behavior. Node syntax and diff whitespace checks passed.
- 2026-10-19: `docker compose config --quiet` and Compose image build succeeded. Dedicated test stack published only127.0.0.1:5051; runtime `id` returned uid10001(sous), gid999(sous).
- 2026-10-19: Backend discovery inside built Linux image with tests mounted read-only passed48 cases in0.600s (46 actual unit/provider/loader tests,2 optional container tests skipped; optional browser class skipped). No host Python/Node needed by runtime image.
- 2026-10-19: Final neutral completion wording verified in two affected WebKit cases — passed in9.893s.
- 2026-10-19: Final Docker rebuild and Compose validation passed. Dedicated stack integration command with SOUS_CONTAINER_TESTS=1 and SOUS_LIVE_OLLAMA=1 —2 tests passed in29.931s. Actual generated JPEG went through container Flask -> host Ollama -> normalized observation. Actual container restart retained session, recipe snapshot, timer deadline and signed-cookie/CSRF acceptance, and paused monitoring.
- Independent Luna reviews verified camera-request lifecycle, snapshot use, audit provenance and Docker packaging. All actionable findings resolved; no open review defects.
- Final normal Compose stack started with separate fresh volume on127.0.0.1:5050. Root page returned HTTP200; active-session API returnednull; `docker compose ps` showed sous-sous-1 running with loopback-only publication. Test stack was stopped without deleting its data volume.
- Container runtime was verified on this macOS Docker Desktop host using a Linux arm64 image. Windows and native Linux hosts were not directly exercised; Linux host-Ollama binding requirements are documented in [Docker setup](docker.md).

## Test environment

- Python 3.12.15 local venv; Flask 3.1.3, requests 2.34.2, Boto3 1.43.115.
- Playwright 1.64.0 with WebKit 27.2 installed under `/private/tmp/sous-webkit`.
- Personal Chrome and other personal browser sessions are prohibited. All browser tests will use a fresh isolated WebKit context/profile.

## Remaining manual checks

- Physical webcam framing and actual microphone recognition/audio output on the user's hardware; all automated camera frames were synthetic and no personal browser was accessed.
- Real cooking accuracy, confidence calibration and intervention usefulness across recipes have not been evaluated by these integration tests.
- OpenAI, Anthropic and Bedrock are covered by mocked request/response tests; live calls need the user's configured model access/credentials.

See [status](../STATUS.md) for progress and [API contract](api.md) for expected behavior.
