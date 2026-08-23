#!/usr/bin/env bash
# One-command start for macOS / Linux.  chmod +x start.sh, then ./start.sh
set -e
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || echo "[!] FFmpeg not found on PATH - install it (brew install ffmpeg / apt install ffmpeg)"

[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo "Ranking Shorts at http://localhost:8000  (Ctrl+C to stop)"
( command -v open >/dev/null && open http://localhost:8000 ) \
  || ( command -v xdg-open >/dev/null && xdg-open http://localhost:8000 ) || true
exec python -m uvicorn backend.app:app --port 8000
