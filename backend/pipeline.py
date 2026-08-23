"""The yt-dlp + FFmpeg pipeline.

Four stages, each a small pure-ish function so they can be tested on their own:

    download_segment  ->  a raw mp4 of just the requested [start,end] window
    process_clip      ->  a normalised 9:16 clip with the rank + title burned in
    stitch            ->  the normalised clips concatenated, in play order
    run_pipeline      ->  orchestrates the three above over a whole request

Design rules baked in on purpose (see README): NO background music and NO sound
effects are ever added. Each clip keeps its own original audio unless `mute` is set.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import settings


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
class PipelineError(RuntimeError):
    """Raised with a human-readable message when a stage fails."""


def _run(cmd: List[str], what: str) -> str:
    """Run a command, raising PipelineError with trimmed stderr on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise PipelineError(f"{what} failed:\n" + "\n".join(tail))
    return proc.stdout


def parse_timecode(value: str) -> float:
    """Accept seconds ('12', '12.5'), 'M:SS', or 'H:MM:SS' and return float seconds."""
    s = str(value).strip()
    if not s:
        raise PipelineError("empty timecode")
    if ":" not in s:
        return float(s)
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 2:
        m, sec = parts
        return m * 60 + sec
    if len(parts) == 3:
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    raise PipelineError(f"bad timecode: {value!r}")


def _escape_fontfile(p: Path) -> str:
    """FFmpeg's drawtext parses ':' and '\\' specially, which breaks Windows paths
    like C:\\fonts\\x.ttf. Use forward slashes and escape the drive-letter colon."""
    s = str(p).replace("\\", "/")
    return s.replace(":", r"\:")


def _drawtext(font: Path, text: str, *, size: int, y: str, color: str = "white",
              border: int = 6) -> str:
    """One centred drawtext filter. `text` is passed inline, so it must already be
    a single line free of characters we don't escape below."""
    # Escape the characters drawtext treats as special inside an inline text= value.
    safe = (text.replace("\\", "\\\\")
                .replace(":", r"\:")
                .replace("'", r"\u2019")   # curly apostrophe dodges quote-escaping traps
                .replace("%", r"\%"))
    return (
        f"drawtext=fontfile='{_escape_fontfile(font)}':text='{safe}':"
        f"fontcolor={color}:fontsize={size}:borderw={border}:bordercolor=black:"
        f"x=(w-text_w)/2:y={y}"
    )


def _wrap_title(title: str, width: int = 20, max_lines: int = 2) -> List[str]:
    """Wrap a title to at most `max_lines`, ellipsising the overflow."""
    title = " ".join((title or "").split())
    if not title:
        return []
    lines = textwrap.wrap(title, width=width) or [title]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "\u2026"
    return lines


# --------------------------------------------------------------------------- #
# probing
# --------------------------------------------------------------------------- #
def has_audio(path: Path) -> bool:
    out = _run([settings.FFPROBE, "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
               "ffprobe (audio check)")
    return bool(out.strip())


# --------------------------------------------------------------------------- #
# stage 1 - download just the requested window
# --------------------------------------------------------------------------- #
def download_segment(url: str, start: str, end: str, out_path: Path,
                     cookies: Optional[str] = None) -> Path:
    """Download only the [start, end] window of `url` to `out_path` (mp4).

    Uses yt-dlp's --download-sections so we never pull a whole 4-minute video for an
    8-second clip. If a platform/URL doesn't support section downloads, it falls back
    to downloading the smallest available mp4 and trimming it with ffmpeg.
    """
    cookies = cookies or settings.COOKIES
    a, b = parse_timecode(start), parse_timecode(end)
    if b <= a:
        raise PipelineError(f"end ({end}) must be after start ({start})")

    out_path = Path(out_path)
    section = f"*{a}-{b}"
    base = [settings.YTDLP, "--no-playlist", "--force-overwrites",
            "--merge-output-format", "mp4",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"]
    if cookies:
        base += ["--cookies", cookies]

    # Preferred path: server-side section download, keyframe-accurate at the cuts.
    try:
        _run(base + ["--download-sections", section, "--force-keyframes-at-cuts",
                     "-o", str(out_path), url], "yt-dlp section download")
        if out_path.exists():
            return out_path
    except PipelineError:
        pass  # fall through to full-download + trim

    # Fallback: grab the whole thing, then trim with ffmpeg.
    full = out_path.with_suffix(".full.mp4")
    _run(base + ["-o", str(full), url], "yt-dlp full download")
    _run([settings.FFMPEG, "-y", "-ss", str(a), "-to", str(b), "-i", str(full),
          "-c", "copy", str(out_path)], "ffmpeg trim")
    try:
        full.unlink()
    except OSError:
        pass
    if not out_path.exists():
        raise PipelineError(f"download produced no file for {url}")
    return out_path


# --------------------------------------------------------------------------- #
# stage 2 - normalise to 9:16 and burn rank + title (single encode)
# --------------------------------------------------------------------------- #
def _vertical_chain(fill: str) -> str:
    """Video filter that turns any source into an exact WxH 9:16 frame, ending on a
    single [v] label ready for drawtext to append to."""
    W, H, FPS = settings.WIDTH, settings.HEIGHT, settings.FPS
    if fill == "crop":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps={FPS},format=yuv420p[v]")
    # blur: scaled+blurred fill behind the whole letterboxed frame
    return (
        f"[0:v]split=2[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"gblur=sigma=22,setsar=1[bg];"
        f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p[v]"
    )


def process_clip(in_path: Path, out_path: Path, rank: int, title: str,
                 fill: str = "blur", mute: bool = False) -> Path:
    """One ffmpeg pass: source -> 9:16 -> rank badge + title burned -> normalised mp4.

    Every output has identical codec params (h264 / yuv420p / WxH / FPS and aac stereo),
    which is what lets `stitch` concatenate them without re-encoding.
    """
    in_path, out_path = Path(in_path), Path(out_path)
    font = settings.FONT

    chain = _vertical_chain(fill)
    label = "[v]"
    overlays = []
    # Rank badge, top-centre.
    overlays.append(_drawtext(font, f"#{int(rank)}", size=104, y="70", color="white"))
    # Title, wrapped, just under the badge.
    y = 210
    for line in _wrap_title(title):
        overlays.append(_drawtext(font, line, size=60, y=str(y), color="white"))
        y += 74

    if overlays:
        # append drawtexts onto the [v] chain
        chain = chain[:-3] + "," + ",".join(overlays) + "[vout]"
        vmap = "[vout]"
    else:
        chain = chain[:-3] + "[vout]"
        vmap = "[vout]"

    cmd = [settings.FFMPEG, "-y", "-i", str(in_path),
           "-filter_complex", chain, "-map", vmap]

    src_has_audio = (not mute) and has_audio(in_path)
    if mute or not src_has_audio:
        # Synthesise a silent track so every normalised clip has the same stream
        # layout (keeps the concat copy-safe). Still: no music, no effects - silence.
        cmd = [settings.FFMPEG, "-y", "-i", str(in_path),
               "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
               "-filter_complex", chain, "-map", vmap, "-map", "1:a", "-shortest"]
    else:
        cmd += ["-map", "0:a", "-ar", "44100", "-ac", "2"]

    cmd += ["-c:v", "libx264", "-preset", settings.PRESET, "-crf", str(settings.CRF),
            "-pix_fmt", "yuv420p", "-r", str(settings.FPS),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]
    _run(cmd, f"ffmpeg process clip (rank #{rank})")
    return out_path


# --------------------------------------------------------------------------- #
# stage 3 - stitch in play order
# --------------------------------------------------------------------------- #
def stitch(clips: List[Path], out_path: Path) -> Path:
    """Concatenate normalised clips in the given order into one mp4.

    All inputs share identical params, so the concat demuxer copies streams (fast,
    lossless). If a copy ever fails, it re-encodes as a fallback.
    """
    out_path = Path(out_path)
    listfile = out_path.with_suffix(".txt")
    listfile.write_text(
        "".join(f"file '{Path(c).resolve().as_posix()}'\n" for c in clips),
        encoding="utf-8",
    )
    try:
        _run([settings.FFMPEG, "-y", "-f", "concat", "-safe", "0",
              "-i", str(listfile), "-c", "copy", "-movflags", "+faststart",
              str(out_path)], "ffmpeg concat (copy)")
    except PipelineError:
        _run([settings.FFMPEG, "-y", "-f", "concat", "-safe", "0",
              "-i", str(listfile),
              "-c:v", "libx264", "-preset", settings.PRESET, "-crf", str(settings.CRF),
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
              "-movflags", "+faststart", str(out_path)], "ffmpeg concat (re-encode)")
    finally:
        try:
            listfile.unlink()
        except OSError:
            pass
    return out_path


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
@dataclass
class ClipSpec:
    url: str
    start: str
    end: str
    rank: int
    title: str = ""


ProgressCB = Callable[[str, float, int], None]  # (stage, progress 0..1, clips_done)


def run_pipeline(job_id: str, clips: List[ClipSpec], out_path: Path,
                 fill: str = "blur", mute: bool = False,
                 progress: Optional[ProgressCB] = None) -> Path:
    """Download, process and stitch every clip. `clips` is already in play order."""
    def report(stage: str, frac: float, done: int):
        if progress:
            progress(stage, frac, done)

    work = settings.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    n = len(clips)
    processed: List[Path] = []

    for i, c in enumerate(clips):
        report(f"Downloading clip {i + 1}/{n} (#{c.rank})", i / (n + 1), i)
        raw = download_segment(c.url, c.start, c.end, work / f"raw_{i:02d}.mp4")

        report(f"Rendering clip {i + 1}/{n} (#{c.rank})", (i + 0.5) / (n + 1), i)
        out = process_clip(raw, work / f"clip_{i:02d}.mp4", c.rank, c.title,
                           fill=fill, mute=mute)
        processed.append(out)

    report("Stitching final video", n / (n + 1), n)
    stitch(processed, out_path)
    report("Done", 1.0, n)
    return out_path
