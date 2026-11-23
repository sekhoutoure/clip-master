#!/usr/bin/env bash
# CLIP MASTER installer for Linux and macOS. Safe to run again (idempotent).
#
#   ./install.sh                    set everything up (venv, packages, models, third-party tools)
#   ./install.sh --check            doctor: report what is missing, change nothing
#   ./install.sh --yes              also install missing system packages (ffmpeg, node) without asking
#   ./install.sh --with-reference   also fetch the reference-only sources of third_party/manifest.yml
#   ./install.sh --cpu-torch        install the small CPU-only PyTorch build first (no GPU, ~200 MB instead of ~2 GB)
#   ./install.sh --python python3.12   use a specific interpreter
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

CHECK=0; YES=0; REF=0; CPU=0; PY_REQ=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --yes|-y) YES=1 ;;
    --with-reference) REF=1 ;;
    --cpu-torch) CPU=1 ;;
    --python) shift; PY_REQ="${1:-}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option '$1' (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
trap 'die "install failed at line $LINENO. Fix the message above and run ./install.sh again (it is safe to re-run)."' ERR

py_ok() { "$1" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' 2>/dev/null; }
find_python() {
  if [ -n "$PY_REQ" ]; then command -v "$PY_REQ" || die "interpreter '$PY_REQ' not found"; return; fi
  for c in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then command -v "$c"; return; fi
  done
  for c in python3 python; do   # last resort: a newer Python (works for most packages, MediaPipe may lack wheels)
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)'; then
      warn "no Python 3.10-3.12 found, using $($c --version 2>&1). Some wheels (MediaPipe, PyTorch) may be missing."
      command -v "$c"; return
    fi
  done
  die "Python 3.10-3.12 is required. Install it (Debian/Ubuntu: sudo apt install python3.12 python3.12-venv | macOS: brew install python@3.12) and re-run."
}

VENV_PY=".venv/bin/python"

if [ "$CHECK" = 1 ]; then
  if [ -x "$VENV_PY" ]; then exec "$VENV_PY" bootstrap.py --check; fi
  PY="$(find_python)"; exec "$PY" bootstrap.py --check
fi

say "1/5 Python"
PY="$(find_python)"; echo "using $PY ($($PY --version 2>&1))"

say "2/5 System tools (ffmpeg, node)"
pm_install() {  # $1 = description, rest = packages per manager
  local what="$1"; shift
  local sudo=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo="sudo"
  if [ "$YES" != 1 ]; then warn "$what not found. Re-run with --yes to install it automatically, or install it yourself."; return 1; fi
  if   command -v brew    >/dev/null 2>&1; then brew install "${1:-$what}"
  elif command -v apt-get >/dev/null 2>&1; then $sudo apt-get update -y && $sudo apt-get install -y "${2:-$what}"
  elif command -v dnf     >/dev/null 2>&1; then $sudo dnf install -y "${3:-$what}"
  elif command -v pacman  >/dev/null 2>&1; then $sudo pacman -S --noconfirm "${4:-$what}"
  else warn "no supported package manager found for $what"; return 1; fi
}
if command -v ffmpeg >/dev/null 2>&1; then echo "ffmpeg: $(ffmpeg -version | head -1)"
else pm_install ffmpeg ffmpeg ffmpeg ffmpeg ffmpeg || warn "continuing: the bundled imageio-ffmpeg binary will be used (install system ffmpeg for best results)"; fi
if command -v node >/dev/null 2>&1; then echo "node: $(node --version)"
else pm_install "node (optional, Remotion only)" node "nodejs npm" nodejs nodejs || warn "continuing without Node.js: Remotion captions stay disabled"; fi

say "3/5 Virtualenv and Python packages"
if [ ! -x "$VENV_PY" ]; then "$PY" -m venv .venv || die "could not create .venv (Debian/Ubuntu: sudo apt install python3-venv)"; fi
"$VENV_PY" -m pip install --quiet --upgrade pip
if [ "$CPU" = 1 ] && [ "$(uname -s)" = "Linux" ]; then
  say "CPU-only PyTorch"
  "$VENV_PY" -m pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu
fi
"$VENV_PY" -m pip install -r requirements.txt
"$VENV_PY" -m pip install --quiet -e . --no-deps 2>&1 | tail -n 3

say "4/5 Models, Remotion, third-party tools"
ARGS=(); [ "$REF" = 1 ] && ARGS+=(--with-reference)
"$VENV_PY" bootstrap.py "${ARGS[@]+"${ARGS[@]}"}"

say "5/5 Done"
cat <<'MSG'

CLIP MASTER is installed.
  Start:   ./start.sh          (then open http://127.0.0.1:7866)
  Doctor:  ./install.sh --check
  Config:  edit .env  (GROQ_API_KEY is optional: without it the app uses local Ollama)
MSG
