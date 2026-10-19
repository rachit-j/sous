# Validation evidence

## Current state

Implementation in progress. B1 backend tests verified; UI and provider adapters pending.

## Observed checks

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

## Test environment

- Python 3.12.15 local venv; Flask 3.1.3, requests 2.34.2, Boto3 1.43.115.
- Playwright 1.64.0 with WebKit 27.2 installed under `/private/tmp/sous-webkit`.
- Personal Chrome and other personal browser sessions are prohibited. All browser tests will use a fresh isolated WebKit context/profile.

## Planned checks

- Flask unittest suite: state/persistence, CSRF/origin/input validation, timers, provider normalization, transition thresholds, and late results.
- Browser runtime: setup, manual cooking loop, correction, timer expiry, mode controls, reload/recovery, permissions/fallback, and completion.
- JavaScript syntax check using Node; visual inspection in a real browser.
- Live provider smoke tests only when credentials and a configured image-capable model are available; explicitly mark unavailable integrations unverified.

See [status](../STATUS.md) for progress and [API contract](api.md) for expected behavior.
