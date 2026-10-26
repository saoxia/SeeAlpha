#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source ./activate.sh
if ! command -v uv >/dev/null 2>&1; then
    curl --fail --location https://astral.sh/uv/0.13.5/install.sh -o .local/tmp/install-uv.sh
    UV_UNMANAGED_INSTALL="$PWD/.local/bin" sh .local/tmp/install-uv.sh
fi
uv python install 3.14.9
uv sync --locked --python 3.14.9
uv pip check --python .venv/bin/python
.venv/bin/python -c 'import sys,nautilus_trader; print(sys.version); print(nautilus_trader.__version__)'
