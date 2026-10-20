FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Allowlist: only runtime files. Docs, agent notes, tests and dev requirements stay out.
COPY accounts.py app.py library.py providers.py recipe_ai.py recipes.py settings.py voice.py ./
COPY recipes/ ./recipes/
COPY templates/ ./templates/
COPY static/ ./static/

# SQLite database, the generated secret key and the encrypted per-account vaults (users/<id>/settings.vault;
# a V1 settings.vault is preserved untouched) live here (mounted as a named volume by compose.yaml).
RUN useradd --system --uid 10001 --no-create-home sous \
    && mkdir -p /app/instance \
    && chown sous:sous /app/instance
USER sous

EXPOSE 5000

# One worker is required: the per-session analysis lock, the unlocked account vaults, the login
# throttle and the voice stream limits are process-local. Threads handle concurrent requests; a voice WebSocket
# holds one thread for its lifetime (at most 1 active + 2 authenticating), so 8 leaves room for
# normal requests. --timeout is gunicorn's worker heartbeat timeout, not a per-request limit;
# 210 s is above the 180 s local-model read timeout.
# --graceful-timeout lets in-flight requests finish on shutdown/restart.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--worker-class", "gthread", "--threads", "8", "--timeout", "210", "--graceful-timeout", "210", "app:create_app()"]
