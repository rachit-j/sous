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
