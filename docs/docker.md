# Docker Compose

Run Sous in a container with no Python or Node installed on the host. You still need Docker Engine or Docker Desktop with Compose. Docker does not ship a model or the hardware to run one: Sous reaches your own Ollama service or a cloud provider.

## Run

```sh
docker compose up --build
```

Open <http://127.0.0.1:5050>. The port is published on loopback only, so other machines cannot reach it. Use `SOUS_PORT=5051 docker compose up --build` (or set `SOUS_PORT` in `.env`) for another host port. 5050 is the default because macOS AirPlay occupies 5000.

No `.env` or provider credentials are required to start. Without them you can still use manual steps and timers and type a model ID in the interface; visual monitoring needs a reachable provider.

```sh
docker compose up -d --build   # run in the background
docker compose logs -f sous    # follow logs
docker compose stop            # stop; data is kept
docker compose up -d           # start again without rebuilding
```

Rebuild after changing application code, recipes or `requirements.txt`: `docker compose up -d --build`.

## Persistent data

The named volume `sous-instance` is mounted at `/app/instance` and holds the SQLite database, generated session-signing secret, encrypted provider vault and nonsecret preferences. It survives `stop`, `down`, rebuilds and restarts, so sessions, timers, recipe snapshots and settings persist. Monitoring is paused after restart and needs Resume; the credential vault locks and needs your passphrase. Avoid `docker compose down -v` or `docker volume rm`: they delete all Sous data.

## Configuration

Copy `.env.example` to `.env` and set only what you use. Compose reads it for interpolation and passes these variables to the server process (never to the browser, never baked into the image):

- `OLLAMA_BASE_URL` (default `http://host.docker.internal:11434`), `OLLAMA_MODEL` (default `qwen3-vl:8b`)
- `OPENAI_API_KEY`, `OPENAI_MODEL`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
- `AWS_REGION`, `BEDROCK_MODEL_ID`, and optionally `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`

Variables you leave unset are not passed into the container. After editing `.env`, run `docker compose up -d` to recreate the container. Do not paste keys into commands, logs or issue reports.

### Host Ollama

`localhost` inside a container is the container itself, which is why `.env.example` leaves `OLLAMA_BASE_URL` commented out and Compose supplies `host.docker.internal`. Compose maps that name to the host with `host-gateway`.

- Docker Desktop (macOS, Windows): works with Ollama's default settings.
- Linux: Ollama bound only to `127.0.0.1` is not reachable from the Docker bridge network. Make it listen on an address the bridge can reach (for example `OLLAMA_HOST=0.0.0.0:11434` or the bridge IP) and firewall it appropriately. Sous does not change host network settings. The model must already be installed (`ollama pull qwen3-vl:8b` is your own step); the first request can take up to 180 s while a cold model loads.

### AWS Bedrock

Use the in-app **Settings** page to save credentials encrypted with your unlock passphrase, set region and choose a model. No `.env` is required. Unlock after every server restart; keep your passphrase separately because it is not recoverable. The app does not store the unlock key alongside its data. See [secure settings](settings.md).

Sous does not mount `~/.aws`, and `AWS_PROFILE` is not passed. Legacy environment options remain:

- Pass temporary or static keys through `.env`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optionally `AWS_SESSION_TOKEN`, plus `AWS_REGION` and `BEDROCK_MODEL_ID`.
- For SSO or named profiles, run Sous directly with `flask --app app run` where the profile works, or export short-lived credentials from your tooling into `.env`.
- If you deliberately want a credentials directory, add your own read-only bind mount in a local `compose.override.yaml` and set `AWS_PROFILE` there. That is your choice and is not configured by default.

## Runtime notes

- Gunicorn runs one worker with four threads. Do not scale it: the one-analysis-per-session lock and the startup pause of monitoring are process-local.
- The process runs as the non-root user `sous` (uid 10001). Gunicorn's worker timeout is 210 s (a heartbeat limit, not a hard per-request limit; provider calls have their own timeouts, 180 s for local Ollama). On stop or restart, in-flight requests get up to 210 s to finish (Compose waits up to 220 s), so stopping during a cold-model analysis can take a while.
- The image contains only runtime code, templates, static files and `recipes/*.json`; docs, tests and agent files are excluded by `.dockerignore` and the `COPY` allowlist.
- There is no container healthcheck: the slim image has no `curl`, and none is added just for that. Check `http://127.0.0.1:5050/api/config` instead.
- The app only accepts `localhost`, `127.0.0.1` and `[::1]` Host headers, so use those to browse. Camera access works on `localhost` origins.

## Optional container tests

`tests/test_container.py` is skipped unless `SOUS_CONTAINER_TESTS=1`. It targets a disposable stack on port 5051, never your default one:

```sh
SOUS_PORT=5051 docker compose -p sous-verify up -d --build
SOUS_CONTAINER_TESTS=1 python -m unittest tests.test_container -v
SOUS_PORT=5051 docker compose -p sous-verify down        # keeps the volume
```

The tests only operate on the reserved disposable stack: `SOUS_CONTAINER_URL` must be an `http` loopback URL on port 5051, and before changing anything the test confirms through `docker compose -p sous-verify port sous 5000` that this port belongs to the `sous-verify` project. This keeps fixture cleanup (which ends the active session) away from your default app on 5050.

`SOUS_LIVE_OLLAMA=1` additionally runs a real frame through the container to host Ollama (needs `qwen3-vl:8b` installed). The frame is generated by the dev Playwright WebKit, which must be installed under `/tmp`; the test fails rather than falling back to any other browser or profile:

```sh
TMPDIR=/tmp PLAYWRIGHT_BROWSERS_PATH=/tmp/sous-webkit SOUS_CONTAINER_TESTS=1 SOUS_LIVE_OLLAMA=1 .venv/bin/python -m unittest tests.test_container -v
```

See [providers](providers.md) and the [API contract](api.md).
