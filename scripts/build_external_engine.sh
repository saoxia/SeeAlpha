#!/usr/bin/env bash
set -euo pipefail

# Standalone Engine only: this script never reads the Platform's engine/ tree.
engine_sha="${1:-b33769d432c17fff2b743c31c7efdd6bf3cb450d}"
engine_remote="${SEEALPHA_ENGINE_GIT_REMOTE:-git@github.com:saoxia/seealpha-engine.git}"

if [[ ! "$engine_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Expected a complete 40-character lowercase Engine commit SHA" >&2
  exit 2
fi
for command in git docker; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required tool: $command" >&2
    exit 2
  fi
done

image="seealpha-engine:${engine_sha:0:12}"
checkout_dir="$(mktemp -d "${TMPDIR:-/tmp}/seealpha-engine-build.XXXXXXXX")"
trap 'rm -rf "$checkout_dir"' EXIT

# Auth for this private repo must be configured via SSH or git credential helper.
git clone --quiet --no-checkout "$engine_remote" "$checkout_dir/repository"
git -C "$checkout_dir/repository" checkout --quiet --detach "$engine_sha"
actual="$(git -C "$checkout_dir/repository" rev-parse HEAD)"
if [[ "$actual" != "$engine_sha" ]]; then
  echo "Independent Engine revision mismatch; refusing to build" >&2
  exit 1
fi

# A v2 change must be explicitly coordinated across both repositories.
# Guard accidental contract drift before building or deploying a candidate.
platform_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for contract in core/engine_contracts.py core/trading_contracts.py; do
  if ! cmp -s "$platform_root/$contract" "$checkout_dir/repository/$contract"; then
    echo "Engine/Platform contract mismatch: $contract; refusing to build" >&2
    exit 1
  fi
done

(
  cd "$checkout_dir/repository"
  docker build -f docker/engine.Dockerfile -t "$image" .
)

printf '\nBuilt %s from Engine commit %s\n' "$image" "$engine_sha"
printf 'Set ENGINE_DOCKER_IMAGE=%s in your isolated Platform deployment.\n' "$image"
printf 'No tenant containers or state volumes were changed.\n'
