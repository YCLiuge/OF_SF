#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$root/runtime-linux/bin/python3.12" ]; then
    unset PYTHONPATH PYTHONHOME
    exec "$root/runtime-linux/bin/python3.12" -E -s -B "$root/of.py" "$@"
fi
exec python3 -B "$root/of.py" "$@"
