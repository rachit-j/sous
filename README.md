# Sous

A Flask cooking companion for a small trusted group, with separate accounts and AWS connections. Scrambled eggs is the first bundled recipe. A webcam supplies periodic snapshots; Sous tracks the recipe, speaks useful updates, and lets the cook correct its understanding. V2 is a browser app; native iOS and TestFlight are deferred.

## Run with Docker

```sh
docker compose up --build
```

Open http://127.0.0.1:5050 and create the first administrator, then create other accounts in Settings. No host Python or Node setup is needed. Data persists in a named volume. Each user configures their own AWS Bedrock connection and vault passphrase in **Settings**; shared environment cloud keys are not used. Ollama remains an optional shared local service. See [accounts](docs/accounts.md), [secure settings](docs/settings.md) and [Docker setup](docs/docker.md).

## Local Flask development

Use Python 3.12 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -r requirements.txt
source .venv/bin/activate
flask --app app run --debug --host 127.0.0.1 --port 5050
```

Open http://127.0.0.1:5050. Port 5050 avoids the macOS AirPlay service commonly using 5000. Configure Bedrock through **Settings**, or use existing environment configuration for other providers, and allow webcam access when ready. The interface and manual controls work without model credentials; real monitoring needs a reachable configured provider. See [provider setup](docs/providers.md).

An optional `.env` can configure the local Ollama URL/model and host port. Account users configure AWS through Settings. V1 data stays on disk but is not silently assigned to new accounts; see [migration boundaries](docs/accounts.md#existing-v1-data).

For a standard Python setup, create `.venv` with `python3 -m venv .venv`, activate it, and use `python -m pip install -r requirements.txt`.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/voice.js
node --check static/voice-worklet.js
```

Optional browser tests use only an isolated WebKit installation and profile under `/tmp`:

```sh
uv pip install -r requirements-dev.txt
PLAYWRIGHT_BROWSERS_PATH=/tmp/sous-webkit .venv/bin/python -m playwright install webkit
TMPDIR=/tmp SOUS_BROWSER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=/tmp/sous-webkit .venv/bin/python -m unittest tests.test_browser tests.test_browser_settings tests.test_browser_voice -v
```

Add `SOUS_LIVE_OLLAMA=1` and select the optional live test to exercise your existing `qwen3-vl:8b` with generated test frames. The normal browser suite mocks model output and camera permission; it never accesses personal browsers or physical cameras.

`SOUS_VOICE_SOCKET_TESTS=1 .venv/bin/python -m unittest tests.test_voice_socket -v` additionally verifies real localhost WebSockets against a synthetic upstream without calling AWS.

See [validation evidence](docs/validation.md) for observed checks and limitations. The test suite uses mocked model responses; it does not send kitchen images to remote providers.

## Conversational voice

For natural back-and-forth guidance, save and unlock your AWS connection in **Settings**, select Tiffany or Matthew, then explicitly start the **Voice companion** during a recipe. Voice uses Amazon Nova 2 Sonic independently of the camera model. Ask questions naturally; speaking interrupts Sous. Use headphones if speaker audio causes echo.

Your AWS region must support Nova 2 Sonic (`us-east-1`, `us-west-2`, `eu-north-1` or `ap-northeast-1`), with permission for `bedrock:InvokeModel` on `amazon.nova-2-sonic-v1:0`. Existing vision-model access does not guarantee voice access. Voice sends microphone audio to AWS while active and incurs AWS usage charges. Stop it when finished; calls end before the eight-minute service limit and require an explicit restart. See [AWS's API permissions](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModelWithBidirectionalStream.html).

The companion offers guidance using recipe state and recent camera evidence; use buttons or text commands to change steps/timers. It does not continuously see video. Audio and conversational transcripts are not saved by Sous. Settings navigation, a hidden tab, session end or vault locking stops voice. The optional browser voice-command mode remains available when the companion is off. See [voice details](docs/voice.md).

## Local data and limits

Add recipes as JSON data; the bundled scrambled-eggs recipe is just the first example. See [recipe format](docs/recipes.md). Tracking and model prompts use the selected recipe's own instructions and visual cues.

SQLite account/session history, per-user encrypted vaults and the persistent Flask secret live under ignored `instance/`. Images and raw audio are not stored by Sous. AWS receives snapshots when selected. Browser speech recognition may use an external service; it is optional.

Bedrock credentials entered in Settings are encrypted using your unlock passphrase; unlock after server restart. The unlock key is held only in server memory. Settings also controls the snapshot interval:10 seconds by default, adjustable from1–15 seconds. Only one analysis runs at a time.

Keep the cooking page open for timer alerts. Returning to the page restores an overdue timer and acknowledges it once; there is no background notification service. Resume monitoring explicitly after a reload or restart.

Sous is an experimental assistant, cannot verify food safety, and does not replace supervision.

## Project references

- [Plan](PLAN.md)
- [Current status](STATUS.md)
- [Documentation index](docs/README.md)
- [Agent workflow](AGENTS.md)
- [Agent communications](agents/communications.md)
