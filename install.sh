#!/usr/bin/env bash
# Install Steed from this folder, or bring it up to date.
#
#   ./install.sh            set up .venv, install packages, add Steed to the menu
#   ./install.sh --update   get the newest Steed (git), then the same
#   ./install.sh --default  also make Steed your default browser
#
# Nothing is installed outside this folder except the menu entry
# (~/.local/share/applications/steed.desktop).
set -euo pipefail
cd "$(dirname "$0")"

update=0
make_default=0
for argument in "$@"; do
  case "$argument" in
    --update) update=1 ;;
    --default) make_default=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $argument (try --help)" >&2; exit 2 ;;
  esac
done

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Steed needs Python 3.10 or newer (python3)." >&2
  exit 1
fi

if [ "$update" = 1 ]; then
  if [ ! -d .git ]; then
    echo "This folder is not a git checkout, so it cannot update itself." >&2
    exit 1
  fi
  echo "Getting the newest Steed…"
  git pull --ff-only
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating the Python environment (.venv)…"
  python3 -m venv .venv
fi
echo "Installing Steed's packages…"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

chmod +x steed
.venv/bin/python -m browser.install_launcher

if [ "$make_default" = 1 ]; then
  .venv/bin/python -c 'from browser import default_browser; print(default_browser.make_default())'
fi

echo
if [ "$update" = 1 ]; then
  echo "Steed is up to date. Restart it to use the new version."
else
  echo "Steed is installed. Open it from your applications menu, or run ./steed."
  echo "Make it your default browser in Settings → General, or run ./install.sh --default."
fi
