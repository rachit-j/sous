# Validation evidence

## Current state

Implementation in progress. B1 backend tests verified; UI and provider adapters pending.

## Observed checks

- 2026-10-19: `.venv/bin/python -m unittest discover -s tests -v` — 16 tests passed (0.214 s final B1 run). Coverage includes state persistence, navigation/corrections, timers, CSRF/origin protection, observation threshold/stale-result/busy handling with mocked provider module.
- Docker CLI and Compose are installed; initial `docker info` could not connect because its daemon socket does not exist. Container runtime verification pending.

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
