#!/usr/bin/env bash
set -euo pipefail

RECREATE=true
if [[ "${1:-}" == "--no-recreate" ]]; then
  RECREATE=false
fi

# Find repo root by searching for pyproject.toml upwards
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CURRENT="$SCRIPT_DIR"
while [[ "$CURRENT" != "/" && ! -f "$CURRENT/pyproject.toml" ]]; do
  CURRENT=$(dirname "$CURRENT")
done
if [[ -f "$CURRENT/pyproject.toml" ]]; then
  REPO_ROOT="$CURRENT"
else
  REPO_ROOT="$SCRIPT_DIR"
fi

cd "$REPO_ROOT"
echo "Preparing .venv in $REPO_ROOT"

VENV_PATH="$REPO_ROOT/.venv"
if [[ -d "$VENV_PATH" ]]; then
  if [[ "$RECREATE" == true ]]; then
    echo "Removing existing .venv..."
    rm -rf "$VENV_PATH"
  else
    echo ".venv already exists. Use --no-recreate to skip recreation. Exiting."
    exit 0
  fi
fi

echo "Creating virtual environment..."
python3 -m venv "$VENV_PATH"
PYTHON="$VENV_PATH/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Python not available or venv creation failed. Ensure 'python3' is on PATH." >&2
  exit 1
fi

echo "Upgrading pip / setuptools / wheel..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "Installing package with extras: browser,dev,markdown"
if ! "$PYTHON" -m pip install -e ".[browser,dev,markdown]"; then
  echo "Editable install failed; trying non-editable install..."
  "$PYTHON" -m pip install ".[browser,dev,markdown]"
fi

# Attempt to run uv sync
UV_BIN="$VENV_PATH/bin/uv"
if [[ -x "$UV_BIN" ]]; then
  echo "Running 'uv sync --frozen --extra browser --extra dev --extra markdown'..."
  "$UV_BIN" sync --frozen --extra browser --extra dev --extra markdown
else
  echo "'uv' not found in venv; trying 'python -m uv'..."
  if ! "$PYTHON" -m uv sync --frozen --extra browser --extra dev --extra markdown; then
    echo "Could not run 'uv sync' automatically. Activate the venv and run:"
    echo "source .venv/bin/activate"
    echo "uv sync --frozen --extra browser --extra dev --extra markdown"
  fi
fi

echo "Preinstall finished. .venv ready."
