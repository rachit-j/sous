# Conversational voice

## Goal and boundaries

An opt-in AWS Nova 2 Sonic companion gives natural spoken guidance and answers questions. Speaking interrupts its current response. Use the existing encrypted AWS connection, independently of the vision model. The user chose AWS and full back-and-forth speech with interruption.

- Explicit Start voice before microphone capture; persistent active indicator and Stop control. Stop on non-cooking navigation, hidden tab, exit, session end/replacement, vault lock, logout/revocation, disconnect or timeout.
- No saved audio/transcripts, browser secrets, credentials in URLs, or upstream error details. Only bounded ephemeral text in the UI.
- Guidance cannot mutate recipe state or timers. A single read-only context tool supplies fresh server state when the cook asks about progress, camera evidence or remaining time; it cannot execute actions. Existing manual/text controls remain. Legacy browser voice is available when the companion is off. Two qualifying camera observations still advance; finishing remains manual.
- Server-authoritative recipe snapshot, step, timer and recent observation ground speech. Distinguish earlier camera evidence from current sight. Speak on a new step/actionable alert without narrating every sample.
- Bound calls below AWS's eight-minute limit. No automatic paid reconnection. Cooking controls remain usable on failure.

## Backend contract

Use Flask-Sock and the official asynchronous Bedrock runtime SDK, pinned to version0.12.0 with its AWS CRT HTTP/2 duplex transport. Credentials come only from the signed-in cook's unlocked vault. One active voice stream remains available instance-wide; busy messages reveal no other user's identity. Validate login/session ownership before upgrade, then same-origin CSRF before AWS initialization. Poll login validity during streaming and stop on revocation. Bound frames, queues and timeouts; clean up upstream on every exit.

WebSocket `/api/sessions/<id>/voice`:

- Client first message: `{"type":"start","csrf_token":"…"}`; server uses saved voice.
- Client audio: binary PCM16 little-endian mono16kHz, small bounded chunks. Stop: `{"type":"stop"}` or close.
- Ready: `{"type":"ready","model":"amazon.nova-2-sonic-v1:0","voice":"tiffany","input_sample_rate":16000,"output_sample_rate":24000}`.
- Audio: `{"type":"audio","audio":"<base64 PCM16 mono24kHz>"}`.
- Transcript: `{"type":"transcript","role":"user|assistant","text":"…","final":true|false}`. Escape model text; suppress speculative duplicates.
- Barge-in: `{"type":"interrupted"}`; clear all scheduled playback immediately.
- Terminal: `{"type":"stopped","reason":"…"}` or `{"type":"error","message":"<sanitized guidance>"}`, then close.

Expose safe `voice` Settings metadata and persist validated `voice_id` (`tiffany` default or `matthew`). Saving does not open microphone/stream. Reuse saved AWS region/keys/session token. Explain unsupported region and missing IAM/model access safely.

## Browser and verification

Plain JS AudioWorklet captures/downsamples input; Web Audio plays bounded output. Prevent old speech synthesis/recognition overlap. Stop tracks, worklets, socket and queued playback on teardown, including delayed permission races. Never automatically capture after refresh/Resume.

Mock AWS streams for protocol/auth/interruption/limits/cleanup tests. Isolated WebKit under `/tmp` uses synthetic media and sockets; no physical microphone, personal browser or paid AWS call. Verify Docker dependencies/runtime with synthetic credentials. Audible quality/model access/real interruption require the user's live trial.

Run the localhost WebSocket integration with `SOUS_VOICE_SOCKET_TESTS=1 .venv/bin/python -m unittest tests.test_voice_socket -v`. It creates an isolated temporary app and fake upstream. See [validation evidence](validation.md) for observed test results and the synthetic audio boundary.

## Sources

- [AWS getting started](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-getting-started.html)
- [Input events](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-input-events.html)
- [Output events](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-output-events.html)
- [Duplex examples](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-code-examples.html)
- [Supported regions](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-sonic.html)
- [API and IAM permission](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModelWithBidirectionalStream.html): the bidirectional API requires `bedrock:InvokeModel`.
- [Flask-Sock servers](https://flask-sock.readthedocs.io/en/latest/web_servers.html)

See [Settings](settings.md), [API](api.md), [plan](../PLAN.md), [status](../STATUS.md).


## Guidance modes

V3 ([F3](v3.md#f3-mixed-initiative-modes)). Every context update carries the cook's guidance mode. In *keep an eye out* (monitoring) mode, step changes are not narrated; timers, named timers, camera alerts and mode changes still are. In *talk me through it* (assistive) mode, each new step is introduced. Modes never let voice change cooking state.


## Camera look

V3 ([F5](v3.md#f5-voice-can-look-at-the-camera)). Nova 2 Sonic takes speech only, so the companion "sees" through a second read-only tool, `look_at_camera` (optional `question`, ≤300 characters).

1. Sonic calls the tool. The request thread checks the session is active and guidance is on (otherwise it answers that it could not look), and applies limits: at most 8 looks per stream, 4 s apart, one at a time.
2. The server sends `{"type": "frame_request", "id"}` on this socket. Only this cook's browser answers, with `{"type": "frame", "id", "image": "data:image/jpeg;base64,…"}` from the camera that is already on, or `{"type": "frame", "id", "error": "camera_off"}`. Frames are limited to 1 MiB of JPEG (the socket's message limit is raised to fit). Late or mismatched frames are ignored, and a frame that does not arrive within 6 s counts as camera off.
3. A worker thread runs one Bedrock Converse vision call with the session's model and the shared connection. It has its own short "eyes" prompt with the current step and its visual cue, so audio keeps flowing meanwhile. Timeout is 40 s.
4. Sonic receives `{look, taken: "just now", note}` or `{error}` and answers aloud. The persona says to describe only that result as something seen just now, that it may be wrong, and that it can never confirm doneness or food safety.

Frames and descriptions are never stored or logged. Looks never change steps, confirmations, observations or alerts; automatic progression stays with the monitoring loop. A vault lock, settings change, logout or session end stops the stream as before, and any look in flight is discarded.


## Voice recipe creation

V3 ([F6](v3.md#f6-create-recipes-by-voice)). Recipes → **Create by voice** opens WebSocket `/api/voice/recipe`; no cooking session is needed. It has the same origin check before upgrade, login, first-message CSRF, shared-vault lease, region check, rate limits, one-stream-per-instance rule and stop conditions as the cooking socket, and it also stops when the cook leaves Recipes.

Sonic gets a recipe-planning persona and a single tool, `draft_recipe` (`dish` required; optional `ingredients`, `pantry`, `equipment`, `servings`, `time_budget_minutes`; servings default to 2 and the time budget to 30 minutes). A worker thread runs the existing AI recipe maker: the same `validate_request`, shared model, per-account rate limit and output parser as `POST /api/recipes/generate`. Validation errors are returned before any AWS call. A valid draft goes to the browser as `{"type": "recipe_draft", "recipe", "warnings"}` and fills the editor. Sonic receives only the name and step titles, plus a note that it cannot save. Voice never saves, publishes or starts cooking; saving stays an explicit button press. A vault change while drafting discards the draft.
