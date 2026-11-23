#!/usr/bin/env bash
# Start CLIP MASTER (Linux / macOS). Run ./install.sh first.
cd "$(dirname "${BASH_SOURCE[0]}")"
[ -x .venv/bin/python ] || { echo "No .venv yet: run ./install.sh first" >&2; exit 1; }
exec .venv/bin/python -m clip_master_cli serve "$@"
