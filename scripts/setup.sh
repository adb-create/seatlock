#!/usr/bin/env bash
# One-time setup: virtual environment, packages and a .env file.
# Run from the project root:  bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# The code needs Python 3.10+ (macOS's built-in python3 can be 3.9).
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3.10 or newer not found."
  echo "Install it with:  brew install python@3.12   (or from python.org)"
  exit 1
fi
echo "Using $($PYTHON --version) ($(command -v $PYTHON))"

if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
  echo "Created .venv"
fi
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements-dev.txt
echo "Packages installed"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

# CI workflow lives in scripts/ci.yml; GitHub needs it under .github/workflows.
if [ ! -f .github/workflows/ci.yml ]; then
  mkdir -p .github/workflows && cp scripts/ci.yml .github/workflows/ci.yml
  echo "Added GitHub Actions workflow"
fi

# Start a git repo (commits use YOUR git name/email).
if [ ! -d .git ] && command -v git >/dev/null 2>&1; then
  git init -q -b main
  if git config user.name >/dev/null && git config user.email >/dev/null; then
    git add -A && git commit -qm "SeatLock: concurrency-safe seat booking API" && echo "Created git repo with first commit"
  else
    echo "Git repo created. Set your identity, then commit:"
    echo '  git config --global user.name "Your Name"; git config --global user.email you@example.com'
  fi
fi

echo
echo "Setup done. Next: start the database (Docker task, or see README > Run in VS Code)."
echo "If VS Code asks, pick the interpreter at ./.venv/bin/python"
