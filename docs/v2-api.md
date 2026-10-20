# V2 API delivery contracts

All APIs except authentication/setup require a current server-authorized account. Identity comes from authentication, never request owner fields. State-changing requests require same-origin CSRF protection. All account data is non-cacheable. Model text is untrusted data.

## Accounts

- GET `/api/auth`: `{user:null|{id,username,is_admin},setup_required,csrf_token}`.
- POST `/api/auth/setup`: `{username,password}`; atomically creates/logs in the first administrator, once only.
- POST `/api/auth/login`: `{username,password}`; POST `/api/auth/logout`: `{}`. Return safe auth state and rotated CSRF.
- GET `/api/users`: administrator list; POST `/api/users`: `{username,password,is_admin:false}`.
- POST `/api/auth/password`: `{current_password,new_password}`; administrator PUT `/api/users/<id>/password`: `{password}`. Revoke affected sessions and lock affected vault.

Existing config, Settings and cooking APIs retain their shapes but resolve only the current account's settings/recipes/sessions/vault. Session IDs belonging to other users yield404. Logout revokes cookie replay, stops voice and prevents pending observation writes.

Enforce one active cook per user with a database uniqueness constraint, including concurrent creation. `/api/sessions/active` must recover only the caller's active cook. Capture account, auth-session, recipe revision and vault identity before inference, then recheck inside the result transaction before any state/events/observations write. Explicit vault lock or credential replacement invalidates pending managed AWS results as well as logout/reset. An upstream request already sent cannot be recalled; reject its obsolete result locally. Generation never persists automatically and must reject obsolete credentials/auth before returning a draft.

Retain one active voice stream instance-wide for this private release. Busy responses reveal no other user's identity/session. Validate login and ownership before socket acceptance/AWS initialization, and check auth/vault validity while streaming. Test two-user contention, revocation and background cleanup.

## Recipes and generation

Library IDs are opaque server identifiers. Bundled recipes retain existing IDs. Personal ownership and global edit permission are enforced for every lookup, copy, edit, delete and cook. User-created recipes are private unless an administrator explicitly creates a global recipe. Editing/deleting a catalog entry never changes a cooking snapshot.

- GET `/api/recipes`: `{recipes:[recipe...]}` with scope/can_edit metadata.
- POST `/api/recipes`: `{recipe,scope:"personal"|"global"}` →201 saved recipe. Only admins may request global.
- GET/PUT/DELETE `/api/recipes/<id>`: own personal or accessible global; PUT `{recipe}` preserves identity/scope.
- POST `/api/recipes/<id>/copy`: `{}` →201 personal copy.
- POST `/api/recipes/generate`: `{dish,ingredients,pantry,equipment,servings,time_budget_minutes}`. Pantry is optional; ingredient/pantry/equipment inputs are bounded string arrays, servings integer1–24, time budget integer1–1440. Use caller's saved model and unlocked AWS connection. Return `{recipe,warnings}` without saving anything. Rate/concurrency/timeout/output limits are server-enforced.
- POST `/api/recipes/<id>/scale`: `{servings}` →reviewable `{recipe,warnings}` draft; never mutate original or active session.

Keep V1 `ingredients` as display strings and existing step fields. Recipe metadata may add `servings` (default1), `equipment` (default empty), and optional structured `ingredient_details` aligned with ingredients for numeric scaling. Unknown quantities remain unchanged with a review warning. Never multiply free-text cooking times, temperatures or step instructions. Generation validates the same schema as manual editing; malformed/oversized outputs never enter the catalog.

## Cooking tools

- GET/POST `/api/sessions/<id>/timers`: list `{timers:[...]}` or create `{name,duration_seconds}`. Each timer has `id`, `name`, absolute `deadline_at` and `status` (`running`, `elapsed`, `acknowledged` or `cancelled`); integer seconds1–86400, name1–80 characters and at most8 live timers. POST `/timers/<timer_id>/ack` or `/cancel` is idempotent. Existing singular timer actions remain compatible. No timer mutation advances a step.
- GET `/api/history`: bounded current-user session summaries; GET `/api/history/<id>`: owned session snapshot/events/notes. PUT `/api/history/<id>/note`: `{note}` bounded to4000 characters.
- GET/PUT `/api/profile`: `{pantry,equipment,servings,time_budget_minutes}`. Generation uses explicit current inputs; the UI can prefill from these saved preferences.
- GET/PUT `/api/sessions/<id>/checklist`: ingredient/equipment entries with checked state; PUT uses server-generated item IDs and booleans. Checklist belongs to the cook, not the recipe catalog.

Named timers, notes and preparation changes are authorized against the parent session. Never attach private annotations to the shared recipe object. Timer alerts remain independent, deduplicated, persisted and visible after refresh.

## Advisory monitoring

Session responses add bounded alert records and observation freshness metadata. Alert `{id,category:"hazard"|"possible_emergency",title,evidence,created_at,acknowledged_at}`. POST `/api/sessions/<id>/alerts/<alert_id>/ack` explicitly acknowledges an alert. Alerts do not change steps/timers; absence of alerts is never an all-clear. Deduplicate repeated identical concerns while allowing meaningful escalation.

Observation normalization may add a bounded advisory safety object and visible-scene/obstruction status. Existing old provider responses remain valid. Manual timer alerts do not depend on AI. Model failures, stale/revoked responses and missing/obscured frames cannot produce an implicit healthy state. Web UI combines server freshness with camera visibility, pending and permission state for paused/camera-off/pending/recent/stale/error/obscured labels. Explain that snapshots are intermittent; no continuous emergency monitoring or automated contacts.

This document defines target contracts for delivery slices; [STATUS.md](../STATUS.md) identifies what has actually shipped. See [V2 specification](v2.md).
