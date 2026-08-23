"""Configuration for the Ranking Shorts backend.

Everything here can be overridden with environment variables so the same code
runs on your Windows machine, in a container, or on a server without edits.
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ranking-shorts/
ASSETS = ROOT / "assets"
# Display face for the title, rank numbers and captions. Poppins Bold is the heavy
# geometric sans the reference videos use; DejaVu stays as an automatic fallback.
FONT = Path(os.environ.get("RANKING_FONT", ASSETS / "Poppins-Bold.ttf"))
# Colour-emoji face, so captions like "No fear\U0001F648" render in colour instead of
# as empty boxes. Optional - without it, emoji are skipped.
EMOJI_FONT = Path(os.environ.get("RANKING_EMOJI_FONT", ASSETS / "NotoColorEmoji.ttf"))

# Working areas. WORK holds per-job intermediate files; OUTPUT holds finished videos.
WORK_DIR = Path(os.environ.get("RANKING_WORK_DIR", ROOT / "work"))
OUTPUT_DIR = Path(os.environ.get("RANKING_OUTPUT_DIR", ROOT / "output"))

# Binaries. yt-dlp is resolved from PATH (pip installs a `yt-dlp` script); ffmpeg/ffprobe
# must be installed and on PATH, or point these at the executables explicitly.
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
YTDLP = os.environ.get("YTDLP_BIN") or shutil.which("yt-dlp") or "yt-dlp"

# Optional Netscape-format cookies file. TikTok, Instagram and age-gated YouTube
# often need this; public YouTube usually does not.
COOKIES = os.environ.get("RANKING_COOKIES") or None

# Output geometry. 9:16 vertical.
WIDTH = int(os.environ.get("RANKING_WIDTH", 1080))
HEIGHT = int(os.environ.get("RANKING_HEIGHT", 1920))
FPS = int(os.environ.get("RANKING_FPS", 30))

# x264 quality for the per-clip normalise pass (lower = better/bigger).
CRF = int(os.environ.get("RANKING_CRF", 20))
PRESET = os.environ.get("RANKING_PRESET", "veryfast")

WORK_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
