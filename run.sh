#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p .cache .tmp
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
export TMPDIR="$PWD/.tmp"
export XDG_CACHE_HOME="$PWD/.cache"
if [ ! -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv --python '>=3.11' .venv
        uv pip install --python .venv/bin/python -e .
    else
        python3 -m venv .venv
        .venv/bin/python -m pip install --cache-dir "$PWD/.cache/pip" -e .
    fi
fi
exec .venv/bin/python -m hikvision_console "$@"
