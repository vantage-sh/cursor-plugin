#!/bin/sh
# Runs the Vantage usage hook with Python 3. Without Python 3, it answers Cursor
# and does nothing, so the agent is never blocked.
#
# $1 is the team's VANTAGE_TELEMETRY_TOKEN plugin variable. It is empty, or the
# unexpanded placeholder, when no admin has set one; the hook ignores both.
VANTAGE_CURSOR_TEAM_TOKEN="${1:-}"
export VANTAGE_CURSOR_TEAM_TOKEN

python_path="${VANTAGE_CURSOR_PYTHON:-}"
if [ -z "$python_path" ]; then
  python_path="$(command -v python3 2>/dev/null || true)"
fi

# On macOS, /usr/bin/python3 is a stub that prompts to install developer tools.
if [ "$python_path" = "/usr/bin/python3" ] && command -v xcode-select >/dev/null 2>&1; then
  xcode-select -p >/dev/null 2>&1 || python_path=""
fi

if [ -z "$python_path" ] || [ ! -x "$python_path" ]; then
  cat >/dev/null
  echo "{}"
  exit 0
fi

exec "$python_path" "$(dirname "$0")/vantage_usage.py"
