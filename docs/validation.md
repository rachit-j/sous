# Validation evidence

## Current state

Implementation in progress. No application tests or provider smoke checks have completed yet.

## Planned checks

- Flask unittest suite: state/persistence, CSRF/origin/input validation, timers, provider normalization, transition thresholds, and late results.
- Browser runtime: setup, manual cooking loop, correction, timer expiry, mode controls, reload/recovery, permissions/fallback, and completion.
- JavaScript syntax check using Node; visual inspection in a real browser.
- Live provider smoke tests only when credentials and a configured image-capable model are available; explicitly mark unavailable integrations unverified.

See [status](../STATUS.md) for progress and [API contract](api.md) for expected behavior.
