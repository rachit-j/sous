# Docker Compose

Run Sous in a container with no Python or Node installed on the host. You still need Docker Engine or Docker Desktop with Compose. Docker does not ship a model or the hardware to run one: Sous reaches your own Ollama service or a cloud provider.

## Run

```sh
docker compose up --build
```

Open <http://127.0.0.1:5050>, create the first administrator, and add other accounts in Settings. Each cook supplies their own AWS connection. The port is loopback-only; other machines cannot reach it. Use SOUS_PORT to choose another host port. See [accounts and preserved V1 data](accounts.md).

No `.env` or provider credentials are required to start. Without them you can still use manual steps and timers and type a model ID in the interface; visual monitoring needs a reachable provider.

```sh
docker compose up -d --build   # run in the background
docker compose logs -f sous    # follow logs
docker compose stop            # stop; data is kept
docker compose up -d           # start again without rebuilding
```

Rebuild after changing application code, recipes or `requirements.txt`: `docker compose up -d --build`.

## Persistent data

The named volume sous-instance is mounted at /app/instance and holds SQLite accounts/data, the signing secret, and per-user encrypted vaults beneath users/<id>/. It survives stop/down/rebuild/restart. Monitoring pauses and every vault locks after restart. Preserve the volume: down -v or volume rm deletes all Sous data. V1 single-user rows and the original vault are retained but unassigned to new accounts.

## Configuration

Use .env only for instance-local options such as host port and Ollama. AWS setup belongs to each signed-in account's Settings:

- `OLLAMA_BASE_URL` (default `http://host.docker.internal:11434`), `OLLAMA_MODEL` (default `qwen3-vl:8b`)
- Shared cloud environment keys/defaults are ignored by V2 account-facing calls.

Compose passes only the local Ollama URL/model, with the defaults above. After editing `.env`, run `docker compose up -d` to recreate the container. Do not paste keys into commands, logs or issue reports.

### Host Ollama

`localhost` inside a container is the container itself, which is why `.env.example` leaves `OLLAMA_BASE_URL` commented out and Compose supplies `host.docker.internal`. Compose maps that name to the host with `host-gateway`.

- Docker Desktop (macOS, Windows): works with Ollama's default settings.
- Linux: Ollama bound only to `127.0.0.1` is not reachable from the Docker bridge network. Make it listen on an address the bridge can reach (for example `OLLAMA_HOST=0.0.0.0:11434` or the bridge IP) and firewall it appropriately. Sous does not change host network settings. The model must already be installed (`ollama pull qwen3-vl:8b` is your own step); the first request can take up to 180 s while a cold model loads.

### AWS Bedrock

Use the in-app **Settings** page to save credentials encrypted with your unlock passphrase, set region and choose a model. No `.env` is required. Unlock after every server restart; keep your passphrase separately because it is not recoverable. The app does not store the unlock key alongside its data. See [secure settings](settings.md).

Sous does not mount ~/.aws. V2 never falls back to AWS_PROFILE, server environment keys or another user's connection. Login password changes and administrator resets do not recover or rewrite the independent vault passphrase.

## Runtime notes

- Gunicorn runs one worker with eight threads. One live voice WebSocket holds a thread; active voice is limited to one stream. Do not scale workers: analysis/voice limits and startup monitoring pause are process-local.
- The unlocked credential vault is also process-local. It locks whenever the server worker restarts; unlock again through Settings. Keep the passphrase separate from the volume backup. Locking discards in-memory key references, but cannot recall an AWS request already submitted.
- The process runs as the non-root user `sous` (uid 10001). Gunicorn's worker timeout is 210 s (a heartbeat limit, not a hard per-request limit; provider calls have their own timeouts, 180 s for local Ollama). On stop or restart, in-flight requests get up to 210 s to finish (Compose waits up to 220 s), so stopping during a cold-model analysis can take a while.
- The image contains only runtime code, templates, static files and `recipes/*.json`; docs, tests and agent files are excluded by `.dockerignore` and the `COPY` allowlist.
- The image includes the pinned official AWS streaming SDK and CRT HTTP/2 transport. [Conversational voice](voice.md) uses saved, unlocked credentials and the browser microphone; no host audio-device mount or additional service is required.
- There is no container healthcheck. GET http://127.0.0.1:5050/api/auth checks availability without exposing account data; config/settings require login.
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
