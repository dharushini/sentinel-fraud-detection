#!/usr/bin/env bash
# One-shot dev launcher for Sentinel.
set -euo pipefail
cd "$(dirname "$0")"

# scikit-learn is pinned to the exact version that trained the committed model,
# and that version needs Python 3.11+. Pick the newest suitable interpreter
# (macOS's bundled python3 is often 3.9) instead of failing deep inside pip.
if [ -n "${PYTHON:-}" ]; then
  PY=$PYTHON
else
  PY=""
  for cand in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 && \
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY=$cand; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "✖ Sentinel needs Python 3.11 or newer (found: $(python3 --version 2>&1 || echo none))."
    echo "  Install it from https://www.python.org/downloads/ or with 'brew install python@3.12',"
    echo "  then run ./run.sh again (or: PYTHON=/path/to/python3.12 ./run.sh)."
    exit 1
  fi
fi

# an existing .venv built on an older Python can't install the pinned packages
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "▶ existing .venv uses Python < 3.11 — recreating it"
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  echo "▶ creating virtualenv (.venv)"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "▶ installing dependencies"
pip install -q -r requirements-dev.txt

if [ ! -f sentinel/artifacts/model.joblib ]; then
  echo "▶ training model (first run only, ~10s)"
  python -m sentinel.train
  echo "▶ evaluating on a fresh held-out world"
  python -m sentinel.eval || true
  echo "▶ latency + fairness audit"
  python -m sentinel.audit || true
fi

echo "▶ starting API + dashboard on http://127.0.0.1:8000"
exec uvicorn sentinel.main:app --host 127.0.0.1 --port 8000
