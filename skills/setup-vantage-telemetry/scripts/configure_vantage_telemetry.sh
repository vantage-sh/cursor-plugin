#!/bin/sh
set -eu

python_path="$(command -v python3 2>/dev/null || true)"
if [ -z "$python_path" ]; then
  echo "Python 3 is required to set up Vantage telemetry. Install it, then run setup again." >&2
  exit 1
fi

exec "$python_path" "$(dirname "$0")/configure_vantage_telemetry.py" "$@"
