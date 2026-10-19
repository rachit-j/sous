# Sous

A local Flask cooking companion for a scrambled-eggs session. A laptop webcam supplies periodic snapshots; Sous tracks recipe steps, speaks useful updates, and lets the cook correct its understanding.

## Run locally

Use Python 3.12 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -r requirements.txt
cp .env.example .env
source .venv/bin/activate
flask --app app run --debug --host 127.0.0.1 --port 5050
```

Open http://127.0.0.1:5050. Port 5050 avoids the macOS AirPlay service commonly using 5000. Set a provider's configuration in `.env`, choose its image-capable model in the app, and allow webcam access when ready. The interface and manual controls work without model credentials; real monitoring needs a reachable configured provider. See [provider setup](docs/providers.md).

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

SQLite session history and the persistent Flask secret live under ignored `instance/`. Images and raw audio are not stored by Sous. Hosted providers receive snapshots when selected. Browser speech recognition may use an external service; it is optional.

Keep the cooking page open for timer alerts. Returning to the page restores an overdue timer and acknowledges it once; there is no background notification service. Resume monitoring explicitly after a reload or restart.

Sous is an experimental assistant, cannot verify food safety, and does not replace supervision.

## Project references

- [Plan](PLAN.md)
- [Current status](STATUS.md)
- [Documentation index](docs/README.md)
- [Agent workflow](AGENTS.md)
- [Agent communications](agents/communications.md)
