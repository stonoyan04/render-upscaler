#!/usr/bin/env bash
# Start Render Upscaler from source on macOS or Linux.
# First run creates a private Python environment and installs what is needed.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "First start: setting up (one time, a few minutes)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip -q
  .venv/bin/python -m pip install -r requirements.txt -q
fi
exec .venv/bin/python app.py "$@"
