#!/usr/bin/env bash
# Runs on the deployment host; no source checkout or host Python required.
set -euo pipefail

root=${1:?deployment directory required}
revision=${2:?commit SHA required}
image=${3:?immutable image reference required}
project=${4:?Compose project required}
[[ "$root" =~ ^/[a-zA-Z0-9_/-]+$ ]]
[[ "$revision" =~ ^[a-f0-9]{40}$ ]]
[[ "$image" =~ ^ghcr.io/[a-z0-9_./-]+@sha256:[a-f0-9]{64}$ ]]
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]
release="$root/releases/$revision"
test -f "$root/.env"

# Also serialize manual invocations with Actions deployments.
exec 9> "$root/deploy.lock"
flock -w 600 9
previous=$(readlink -f "$root/current" || true)

compose() {
  docker compose --project-name "$project" --env-file "$root/.env" \
    -f "$release/compose.yaml" -f "$release/compose.production.yaml" "$@"
}

export SOUS_IMAGE="$image"
compose config --quiet
compose pull sous
if ! compose up -d --no-build --wait --wait-timeout 90 sous; then
  echo 'Deployment failed health checks.' >&2
  if [[ -n "$previous" && -f "$previous/image" ]]; then
    release="$previous"
    export SOUS_IMAGE
    SOUS_IMAGE=$(cat "$previous/image")
    echo 'Restoring the previous managed release.' >&2
    compose up -d --no-build --pull never --wait --wait-timeout 90 sous
  else
    echo 'No previous managed release is available for automatic rollback.' >&2
  fi
  exit 1
fi

printf '%s\n' "$image" > "$release/image"
ln -sfn "$release" "$root/current"
echo "Deployed $revision. Nginx keeps routing to the stable loopback port."
