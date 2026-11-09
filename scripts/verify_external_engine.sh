#!/usr/bin/env bash
set -euo pipefail
umask 077

# One-off integration test: build the pinned external Engine, start it with only
# temporary volumes, and import Platform from an api/+core/ only source tree.
# No real accounts, business database, exchange keys, orders or trading.
platform_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
engine_sha="${1:-b33769d432c17fff2b743c31c7efdd6bf3cb450d}"
if [[ ! "$engine_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Expected complete 40-character Engine source SHA" >&2
  exit 2
fi

# Docker Desktop / local unix socket only. Never use a remote Docker host.
docker_host="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
if [[ "$docker_host" != unix://* ]]; then
  echo "Smoke is restricted to local Unix-socket Docker context (actual: $docker_host)" >&2
  exit 2
fi
if [[ ! -x "$platform_root/.venv/bin/python" ]]; then
  echo "Platform Python .venv is missing; run 'uv sync --locked --group api' first" >&2
  exit 2
fi

"$platform_root/scripts/build_external_engine.sh" "$engine_sha"
image="seealpha-engine:${engine_sha:0:12}"
docker image inspect "$image" >/dev/null

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/seealpha-external-engine.XXXXXXXX")"
cid=""
cleanup() {
  if [[ -n "$cid" ]]; then
    docker rm -f "$cid" >/dev/null 2>&1 || true
  fi
  rm -rf "$sandbox"
}
trap cleanup EXIT
mkdir -p "$sandbox"/{state,data,local,logs,platform}
cp -R "$platform_root/api" "$platform_root/core" "$sandbox/platform/"
test ! -e "$sandbox/platform/engine"
test ! -e "$sandbox/platform/nautilus_support.py"

# Disposable secret lives only in a 0600 env file and process environment.
token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat > "$sandbox/engine.env" <<EOF
ENGINE_TOKEN=$token
ENGINE_TENANT_ID=987654321
ENGINE_TENANT_PURPOSE=development
ENGINE_HOST=0.0.0.0
ENGINE_PORT=8766
ENGINE_PROJECT=/app
ENGINE_STATE=/app/.local/state/engine
EOF
chmod 600 "$sandbox/engine.env"
container_name="sa-ext-smoke-$(python3 -c 'import secrets;print(secrets.token_hex(8))')"

cid="$(docker run -d --name "$container_name" \
  --label seealpha.managed=external-engine-smoke \
  --cpus 1 --memory 2g --memory-swap 2g --pids-limit 256 \
  --cap-drop ALL --security-opt no-new-privileges \
  --publish 127.0.0.1::8766 \
  --env-file "$sandbox/engine.env" \
  --mount "type=bind,src=$sandbox/state,dst=/app/.local/state/engine" \
  --mount "type=bind,src=$sandbox/data,dst=/app/data" \
  --mount "type=bind,src=$sandbox/local,dst=/app/.local/run" \
  --mount "type=bind,src=$sandbox/logs,dst=/app/logs" \
  "$image")"

port_binding="$(docker port "$cid" 8766/tcp)"
if [[ ! "$port_binding" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  echo "Engine must expose one loopback-only TCP port, got: $port_binding" >&2
  exit 1
fi
export SEEALPHA_ISOLATED_PLATFORM_ROOT="$sandbox/platform"
export ENGINE_URL="http://$port_binding"
export ENGINE_TOKEN="$token"
export ENGINE_SMOKE_TENANT_ID=987654321

"$platform_root/.venv/bin/python" -I "$platform_root/scripts/verify_external_engine_http.py"

echo "PASS: pinned standalone Engine + isolated Platform HTTP v2 handshake"
echo "Temporary Engine container, token and test volumes are removed on exit."
