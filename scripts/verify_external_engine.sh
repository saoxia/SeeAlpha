#!/usr/bin/env bash
set -euo pipefail
umask 077

# One-off integration test: build the pinned external Engine, start it with only
# temporary volumes, and import Platform from an api/+core/ only source tree.
# No real accounts, business database, exchange keys or live trading.
# Optional backtest exercises only synthetic bars and virtual orders/fills.
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
# Prefer the new Engine-free runtime; retain the previously accepted root-env
# fallback for the read-only smoke during the gradual migration.
platform_python="$platform_root/platform/.venv/bin/python"
if [[ ! -x "$platform_python" ]]; then
  platform_python="$platform_root/.venv/bin/python"
fi
if [[ ! -x "$platform_python" ]]; then
  echo "Install the standalone Platform environment first: uv sync --project platform --locked" >&2
  exit 2
fi
if [[ ( "${SEEALPHA_VERIFY_BUSINESS:-0}" == "1" || "${SEEALPHA_VERIFY_BACKTEST:-0}" == "1" \
      || "${SEEALPHA_VERIFY_PLATFORM_BACKTEST:-0}" == "1" ) \
   && "$platform_python" != "$platform_root/platform/.venv/bin/python" ]]; then
  echo "Writable Engine smokes require the independent Platform runtime (platform/.venv)" >&2
  exit 2
fi

"$platform_root/scripts/build_external_engine.sh" "$engine_sha"
image="seealpha-engine:${engine_sha:0:12}"
docker image inspect "$image" >/dev/null

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/seealpha-external-engine.XXXXXXXX")"
cid=""
api_cid=""
cleanup() {
  if [[ -n "$cid" ]]; then
    docker rm -f "$cid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$api_cid" ]]; then
    docker rm -f "$api_cid" >/dev/null 2>&1 || true
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

"$platform_python" -I "$platform_root/scripts/verify_external_engine_http.py"

# Optional deeper business boundary: writes only a disposable development
# tenant row in the container's temporary Engine SQLite state; no exchange calls.
if [[ "${SEEALPHA_VERIFY_BUSINESS:-0}" == "1" ]]; then
  export SEEALPHA_SMOKE_DISPOSABLE=1
  "$platform_python" -I "$platform_root/scripts/verify_external_engine_business.py"
fi

# Real NautilusTrader historical execution. Seed only the *container's* temporary
# bind mount; the host Python imports no Engine implementation or research tools.
if [[ "${SEEALPHA_VERIFY_BACKTEST:-0}" == "1" ]]; then
  test -f "$platform_root/packages/ema_crossover/seealpha-package.json"
  mkdir -p "$sandbox/platform/packages"
  mkdir -p "$sandbox/platform/packages/ema_crossover"
  for source in seealpha-package.json strategy.py README.md; do
    cp "$platform_root/packages/ema_crossover/$source" "$sandbox/platform/packages/ema_crossover/$source"
  done
  export SEEALPHA_SMOKE_DISPOSABLE=1
  engine_pid_before="$(docker inspect -f '{{.State.Pid}}' "$cid")"
  if [[ ! "$engine_pid_before" =~ ^[1-9][0-9]*$ ]]; then
    echo "Expected a running, resident Engine process" >&2
    exit 1
  fi
  docker exec -i -e SEEALPHA_SMOKE_DISPOSABLE=1 "$cid" python - \
    < "$platform_root/scripts/seed_external_engine_backtest_catalog.py"
  "$platform_python" -I "$platform_root/scripts/verify_external_engine_backtest.py"
  engine_pid_after="$(docker inspect -f '{{.State.Pid}}' "$cid")"
  if [[ "$engine_pid_after" != "$engine_pid_before" ]]; then
    echo "Engine container was restarted during repeated backtests" >&2
    exit 1
  fi
  echo "PASS: same resident Engine process served synchronous and asynchronous backtests"
fi

# Real Platform business API regression: a fresh multi-tenant Engine is needed
# to preserve the original test's production/development/other-tenant cases.
# This is an *integration-test-only* second container, not per-backtest Docker.
if [[ "${SEEALPHA_VERIFY_PLATFORM_BACKTEST:-0}" == "1" ]]; then
  mkdir -p "$sandbox"/{api-state,api-data,api-local,api-logs,business}
  cp -R "$platform_root/migrations" "$sandbox/platform/"
  cp "$platform_root/alembic.ini" "$sandbox/platform/alembic.ini"
  mkdir -p "$sandbox/platform/packages/ema_crossover"
  for source in seealpha-package.json strategy.py README.md; do
    cp "$platform_root/packages/ema_crossover/$source" \
      "$sandbox/platform/packages/ema_crossover/$source"
  done

  api_token="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  cat > "$sandbox/api-engine.env" <<EOF
ENGINE_TOKEN=$api_token
ENGINE_HOST=0.0.0.0
ENGINE_PORT=8766
ENGINE_PROJECT=/app
ENGINE_STATE=/app/.local/state/engine
EOF
  chmod 600 "$sandbox/api-engine.env"
  api_name="sa-api-bff-smoke-$(python3 -c 'import secrets;print(secrets.token_hex(8))')"
  api_cid="$(docker run -d --name "$api_name" \
    --label seealpha.managed=external-engine-smoke \
    --cpus 1 --memory 2g --memory-swap 2g --pids-limit 256 \
    --cap-drop ALL --security-opt no-new-privileges \
    --publish 127.0.0.1::8766 \
    --env-file "$sandbox/api-engine.env" \
    --mount "type=bind,src=$sandbox/api-state,dst=/app/.local/state/engine" \
    --mount "type=bind,src=$sandbox/api-data,dst=/app/data" \
    --mount "type=bind,src=$sandbox/api-local,dst=/app/.local/run" \
    --mount "type=bind,src=$sandbox/api-logs,dst=/app/logs" \
    "$image")"
  api_binding="$(docker port "$api_cid" 8766/tcp)"
  if [[ ! "$api_binding" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
    echo "The business API test Engine must bind only loopback" >&2
    exit 1
  fi
  api_pid_before="$(docker inspect -f '{{.State.Pid}}' "$api_cid")"
  if [[ ! "$api_pid_before" =~ ^[1-9][0-9]*$ ]]; then
    echo "Disposable API Engine was not running" >&2
    exit 1
  fi
  docker exec -i -e SEEALPHA_SMOKE_DISPOSABLE=1 "$api_cid" python - \
    < "$platform_root/scripts/seed_external_engine_backtest_catalog.py"

  # Hard environmental isolation: never read host DB URLs or trading secrets.
  env -i PATH="$PATH" HOME="$sandbox" LANG="${LANG:-C.UTF-8}" \
    PYTHONDONTWRITEBYTECODE=1 SEEALPHA_SMOKE_DISPOSABLE=1 \
    SEEALPHA_ISOLATED_PLATFORM_ROOT="$sandbox/platform" \
    SEEALPHA_API_TEST_STATE_DIR="$sandbox/business" \
    ENGINE_URL="http://$api_binding" ENGINE_TOKEN="$api_token" \
    "$platform_python" -I "$platform_root/scripts/verify_external_platform_backtest_api.py"
  api_pid_after="$(docker inspect -f '{{.State.Pid}}' "$api_cid")"
  if [[ "$api_pid_before" != "$api_pid_after" ]]; then
    echo "The Engine unexpectedly restarted during the migrated API backtest" >&2
    exit 1
  fi
  echo "PASS: migrated API business backtest used one resident independent Engine"
fi

echo "PASS: pinned standalone Engine + isolated Platform HTTP v2 handshake"
echo "Temporary Engine container, token and test volumes are removed on exit."
