#!/usr/bin/env bash

# 将 Python 运行时、依赖、工具、缓存和临时文件都保存在本项目目录中。
if [ -n "${ZSH_VERSION:-}" ]; then
    PROJECT_ROOT="$(cd "$(dirname "${(%):-%N}")" && pwd)"
else
    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

export NAUTILUS_PROJECT_ROOT="$PROJECT_ROOT"
export XDG_CACHE_HOME="$PROJECT_ROOT/.local/cache"
export XDG_DATA_HOME="$PROJECT_ROOT/.local/data"
export XDG_STATE_HOME="$PROJECT_ROOT/.local/state"
export TMPDIR="$PROJECT_ROOT/.local/tmp"
export UV_CACHE_DIR="$PROJECT_ROOT/.local/cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.local/python"
export UV_PYTHON_PREFERENCE="only-managed"
export UV_TOOL_DIR="$PROJECT_ROOT/.local/tools"
export UV_TOOL_BIN_DIR="$PROJECT_ROOT/.local/bin"
export PIP_CACHE_DIR="$PROJECT_ROOT/.local/cache/pip"
export PYTHONUSERBASE="$PROJECT_ROOT/.local/python-user"
export PYTHONPYCACHEPREFIX="$PROJECT_ROOT/.local/cache/pycache"
export MPLCONFIGDIR="$PROJECT_ROOT/.local/cache/matplotlib"
export CARGO_HOME="$PROJECT_ROOT/.local/cargo"
export RUSTUP_HOME="$PROJECT_ROOT/.local/rustup"
export PATH="$PROJECT_ROOT/.local/bin:$CARGO_HOME/bin:$PATH"

# Rust converter artifacts are now owned by the independent Engine checkout.

mkdir -p \
    "$XDG_CACHE_HOME" \
    "$XDG_DATA_HOME" \
    "$XDG_STATE_HOME" \
    "$TMPDIR" \
    "$UV_PYTHON_INSTALL_DIR" \
    "$UV_TOOL_DIR" \
    "$UV_TOOL_BIN_DIR"

if [[ -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

unset PROJECT_ROOT
