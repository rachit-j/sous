# Sous

A local Flask cooking companion driven by structured recipes. Scrambled eggs is the first bundled recipe. A laptop webcam supplies periodic snapshots; Sous tracks the selected recipe's steps, speaks useful updates, and lets the cook correct its understanding.

## Run with Docker

```sh
docker compose up --build
```

Open http://127.0.0.1:5050. No host Python or Node setup is needed. Sessions persist in a named volume. For hosted inference, open **Settings** to configure AWS Bedrock, unlock encrypted credential storage, and select a default model without editing `.env`. Ollama remains an option using your existing host model. See [secure settings](docs/settings.md) and [Docker setup](docs/docker.md).

## Local Flask development

Use Python 3.12 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -r requirements.txt
source .venv/bin/activate
flask --app app run --debug --host 127.0.0.1 --port 5050
```

Open http://127.0.0.1:5050. Port 5050 avoids the macOS AirPlay service commonly using 5000. Configure Bedrock through **Settings**, or use existing environment configuration for other providers, and allow webcam access when ready. The interface and manual controls work without model credentials; real monitoring needs a reachable configured provider. See [provider setup](docs/providers.md).

An optional `.env` can be created from `.env.example` for legacy provider configuration; Bedrock setup through Settings does not require it.

For a standard Python setup, create `.venv` with `python3 -m venv .venv`, activate it, and use `python -m pip install -r requirements.txt`.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
```

Optional browser tests use only an isolated WebKit installation and profile under `/tmp`:

```sh
uv pip install -r requirements-dev.txt
PLAYWRIGHT_BROWSERS_PATH=/tmp/sous-webkit .venv/bin/python -m playwright install webkit
TMPDIR=/tmp SOUS_BROWSER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=/tmp/sous-webkit .venv/bin/python -m unittest tests.test_browser -v
```

Add `SOUS_LIVE_OLLAMA=1` and select the optional live test to exercise your existing `qwen3-vl:8b` with generated test frames. The normal browser suite mocks model output and camera permission; it never accesses personal browsers or physical cameras.

See [validation evidence](docs/validation.md) for observed checks and limitations. The test suite uses mocked model responses; it does not send kitchen images to remote providers.

## Local data and limits

Add recipes as JSON data; the bundled scrambled-eggs recipe is just the first example. See [recipe format](docs/recipes.md). Tracking and model prompts use the selected recipe's own instructions and visual cues.

SQLite session history and the persistent Flask secret live under ignored `instance/`. Images and raw audio are not stored by Sous. Hosted providers receive snapshots when selected. Browser speech recognition may use an external service; it is optional.

Bedrock credentials entered in Settings are encrypted using your unlock passphrase; unlock after server restart. The unlock key is held only in server memory. Settings also controls the snapshot interval:10 seconds by default, adjustable from1–15 seconds. Only one analysis runs at a time.

Keep the cooking page open for timer alerts. Returning to the page restores an overdue timer and acknowledges it once; there is no background notification service. Resume monitoring explicitly after a reload or restart.

Sous is an experimental assistant, cannot verify food safety, and does not replace supervision.

## Project references

- [Plan](PLAN.md)
- [Current status](STATUS.md)
- [Documentation index](docs/README.md)
- [Agent workflow](AGENTS.md)
- [Agent communications](agents/communications.md)
