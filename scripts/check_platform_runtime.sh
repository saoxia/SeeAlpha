#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for the locked Platform runtime" >&2
  exit 2
fi

# Do not install Platform into the old monorepo .venv. Keep dependencies separate.
uv sync --project "$root/platform" --locked
"$root/platform/.venv/bin/python" -m pytest \
  -c "$root/platform/pyproject.toml" -q "$root/platform/tests"
echo "Platform-only dependency/import boundary verified."
