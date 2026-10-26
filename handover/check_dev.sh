#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source ./activate.sh
.venv/bin/python -m ops.check_project --development "$@"
