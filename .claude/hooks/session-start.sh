#!/bin/bash
# Cloud session setup: make sure numpy is there so the tests can run.
# Idempotent, quick, and never fails the session.

if python3 -c "import numpy" >/dev/null 2>&1; then
  echo "session-start: numpy already installed"
elif pip install --quiet numpy >/dev/null 2>&1; then
  echo "session-start: installed numpy"
else
  echo "session-start: could not install numpy (tests that need it will skip)"
fi
exit 0
