# MVP API contract

## Conventions

JSON requests/responses. Error responses: `{ "error": "User-readable message" }` with 400/403/404/409/413/502 as appropriate. No provider secrets or raw upstream errors in responses. Every response that changes a session returns the full session object. Times are Unix seconds (UTC); browser converts for display.

The index template injects a CSRF token in a meta element. POST calls include `X-CSRF-Token`. Require matching browser origin when Origin is present. GET config returns no secrets. Flask browser session cookie persists across reloads; app secret must persist locally across restarts.

## Config and recipes

`GET /api/config` returns `{recipes: [...], providers: [...], sample_interval: 10}`.

Recipe: `{id: "scrambled-eggs", name, description, ingredients: [string], steps: [{title, instruction, visual_cue}]}`. Six zero-indexed steps, defined in PLAN.md.

Provider: `{id: "ollama"|"openai"|"anthropic"|"bedrock", name, configured: boolean, default_model: string}`. Ollama defaults to local URL; cloud entries require server credentials/config. A model ID is required when creating a session. Don't probe or download models automatically.

## Session shape

`{id, recipe_id, provider, model, mode: "monitoring"|"assistive", step_index, status: "active"|"completed"|"ended", monitoring: boolean, revision: integer, created_at, updated_at, step_started_at, timer_deadline: number|null, timer_alerted: boolean, completion_streak: integer, latest_observation: object|null, events: [...]}`.

Events: `{id, kind, message, created_at}` (newest first, at most 50). Observations include `{activity, evidence, confidence, step_complete, alert, created_at}`; provider/model/latency are recorded for evaluation without frames.

`POST /api/sessions`: `{recipe_id, provider, model, mode}` -> 201 session, initially monitoring=false. Only one active session; return 409 if another is active. `GET /api/sessions/active` returns `{session: session|null}` for recovery. `GET /api/sessions/<id>` returns a session.

## Actions

`POST /api/sessions/<id>/actions`: `{action, ...}`.

- `next`, `back`: bounded step navigation. Next on final step does not complete; use Finish.
- `correct`: `step_index` integer and optional `note` string (max 500). Increment revision and reset confirmation streak even if correcting to same step.
- `pause`, `resume`: monitoring state, reset streak/revision. Browser always sends pause on restoration before allowing frames. Server startup also pauses persisted sessions.
- `mode`: `mode` field.
- `timer_set`: `seconds` integer, 1..86400. Replace deadline and reset alerted flag.
- `timer_cancel`: clear deadline and alert state.
- `timer_ack`: only succeeds when deadline has elapsed; marks alerted once and logs completion. Returns unchanged state on duplicate.
- `finish`: permitted on final step; status=completed and monitoring=false.
- `end`: status=ended, monitoring=false. End/finish cancel timers.

Invalid actions and out-of-bounds corrections fail without mutation. Mutating recipe state on ended/completed sessions fails with 409. Revisions invalidate observations after navigation, corrections, monitoring/mode changes, end and finish; timer actions don't invalidate visual evidence.

## Observations

`POST /api/sessions/<id>/observations`: `{image: "data:image/jpeg;base64,...", revision: integer}`. Limit request size to 2 MB and decoded JPEG size to 1 MB; reject invalid base64 and non-JPEG magic. Frontend resizes longest edge to <=960px and uses JPEG quality 0.75.

Server rejects paused/ended or stale inputs. Model executes outside SQLite transactions; recheck revision/status/monitoring atomically on return. Only one analysis per session may run at a time. Return 409 for stale/busy (client refreshes state, no alarm); 502 for provider failures (visible retryable error). Invalid model output never mutates progress. Record sanitized failures for debugging.

Normalized model output: `{activity: string, evidence: string, confidence: number 0..1, step_complete: boolean, alert: string|null}`. Extra keys may be ignored; required fields/types must validate strictly. Confidence must be finite. Model may only report current-step completion, never choose an arbitrary next step or execute actions. Require two consecutive complete observations at >=0.85; all other valid observations reset streak. Advancement resets streak and bumps revision. Final step never auto-completes session.

## Environment

`OLLAMA_BASE_URL` (default http://localhost:11434), `OLLAMA_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `AWS_REGION`, `BEDROCK_MODEL_ID`, optional `AWS_PROFILE`. AWS uses its standard credential chain. Optional `SOUS_SECRET_KEY`, `SOUS_DATABASE` overrides for setup/tests. Load .env server-side. Do not accept arbitrary provider URLs from browser requests.
