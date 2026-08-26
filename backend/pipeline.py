"""The yt-dlp + FFmpeg pipeline.

Stages, each a small pure-ish function so they can be tested on their own:

    download_segment  ->  a raw mp4 of just the requested [start,end] window
    process_clip      ->  a normalised 9:16 clip with a finished overlay composited on
    stitch            ->  the normalised clips concatenated, in play order
    run_pipeline      ->  orchestrates the above over a whole request

The overlay itself is drawn by `overlay.py` (Pillow), not by FFmpeg's drawtext. That
is what makes the ranking *stand*: every clip gets the same list in the same place,
and only the revealed captions and the highlighted row differ between them. Once the
clips are concatenated, the list looks like one continuous element that updates at
each cut instead of text that pops in and out with its own clip.

Design rules baked in on purpose (see README): NO background music and NO sound
effects are ever added. Each clip keeps its own original audio unless `mute` is set.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import library, settings
from .overlay import Row, Style, Word, render_png, rows_for_state


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
# stage 1b - resolve a slot to (file, trim) whichever source it came from
# --------------------------------------------------------------------------- #
def fetch_segment(spec: "ClipSpec", raw_path: Path):
    """Return (path, ss, dur) for one slot, ready to hand to `process_clip`.

    A URL slot is downloaded to `raw_path` and needs no further trim - yt-dlp has
    already cut the window. A library slot returns the source file untouched with
    the trim carried alongside it, so the cut happens inside the single normalise
    pass instead of costing an extra encode and an extra copy of the footage.
    """
    a, b = parse_timecode(spec.start), parse_timecode(spec.end)
    if b <= a:
        raise PipelineError(f"end ({spec.end}) must be after start ({spec.start})")

    if spec.source == "library":
        try:
            src = library.resolve(spec.library_id)
        except library.LibraryError as e:
            raise PipelineError(str(e)) from e
        total, _, _ = library.probe(src)
        if total and a >= total:
            raise PipelineError(
                f"start {spec.start} is past the end of {src.name} "
                f"({total:.1f}s long)")
        if total and b > total + 0.05:
            b = total          # clamp rather than refuse: a rounded end is normal
        return src, a, max(0.05, b - a)

    download_segment(spec.url, spec.start, spec.end, raw_path)
    return raw_path, None, None


# --------------------------------------------------------------------------- #
# stage 2 - normalise to 9:16 and composite the standing overlay
# --------------------------------------------------------------------------- #
def _vertical_chain(fill: str) -> str:
    """Video filter that turns any source into an exact WxH 9:16 frame, ending on a
    single [v] label ready for the overlay to be composited onto."""
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


def process_clip(in_path: Path, out_path: Path, overlay_png: Path,
                 fill: str = "blur", mute: bool = False,
                 ss: Optional[float] = None, dur: Optional[float] = None) -> Path:
    """One ffmpeg pass: source -> 9:16 -> overlay composited -> normalised mp4.

    `overlay_png` is a full-frame RGBA image produced by `overlay.render_png` for the
    ranking state this clip should show. Because it is a still image, FFmpeg holds the
    last (only) frame for the clip's whole duration, so the ranking is rock steady.

    Every output has identical codec params (h264 / yuv420p / WxH / FPS and aac stereo),
    which is what lets `stitch` concatenate them without re-encoding.

    `ss`/`dur` trim the input as part of this same pass. That is how a library file
    is cut: it is already on disk, so pre-trimming it to an intermediate would mean
    encoding the picture twice for nothing. `-ss` goes BEFORE `-i` - ffmpeg still
    seeks accurately there, and putting it after would decode and throw away
    everything from the start of a long source.
    """
    in_path, out_path = Path(in_path), Path(out_path)

    chain = _vertical_chain(fill) + ";[v][1:v]overlay=0:0:format=auto[vout]"
    vmap = "[vout]"

    src_has_audio = (not mute) and has_audio(in_path)
    trim: List[str] = []
    if ss is not None:
        trim += ["-ss", f"{ss:.3f}"]
    if dur is not None:
        trim += ["-t", f"{dur:.3f}"]
    cmd = [settings.FFMPEG, "-y", *trim, "-i", str(in_path), "-i", str(overlay_png)]
    if mute or not src_has_audio:
        # Synthesise a silent track so every normalised clip has the same stream
        # layout (keeps the concat copy-safe). Still: no music, no effects - silence.
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex", chain, "-map", vmap, "-map", "2:a", "-shortest"]
    else:
        cmd += ["-filter_complex", chain, "-map", vmap,
                "-map", "0:a", "-ar", "44100", "-ac", "2"]

    cmd += ["-c:v", "libx264", "-preset", settings.PRESET, "-crf", str(settings.CRF),
            "-pix_fmt", "yuv420p", "-r", str(settings.FPS),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]
    _run(cmd, "ffmpeg process clip")
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
    """A slot: where to get the video, plus where it sits in the standing list.

    `source` is "url" (fetched with yt-dlp) or "library" (already on disk, named by
    an opaque library id). Everything downstream of `fetch_segment` is identical for
    the two, which is why the rest of the pipeline never branches on it.
    """
    url: str
    start: str
    end: str
    rank: int
    caption: str = ""
    color: str = ""
    source: str = "url"
    library_id: str = ""


ProgressCB = Callable[[str, float, int], None]  # (stage, progress 0..1, clips_done)


def _overlay_for(clips: Sequence[ClipSpec], title: Sequence[Word], style: Style,
                 active_pos: int, path: Path) -> Path:
    """Render the ranking's state for the clip at play position `active_pos`."""
    rows = rows_for_state(
        [Row(rank=c.rank, caption=c.caption, color=c.color) for c in clips],
        list(range(len(clips))), active_pos, style.reveal,
    )
    render_png(title, rows, style, path)
    return path


def preview_clip(clips: Sequence[ClipSpec], active_pos: int, out_path: Path,
                 title: Sequence[Word] = (), style: Optional[Style] = None,
                 fill: str = "blur", mute: bool = False,
                 tmp_dir: Optional[Path] = None) -> Path:
    """Render exactly one clip - same download + normalise + overlay as the real
    pipeline - with the ranking in the state it will be in when that clip plays. No
    stitching, since there's only one clip."""
    style = style or Style()
    out_path = Path(out_path)
    tmp_dir = Path(tmp_dir) if tmp_dir else out_path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    spec = clips[active_pos]
    raw = tmp_dir / f"{out_path.stem}_raw.mp4"
    png = tmp_dir / f"{out_path.stem}_ov.png"
    try:
        src, ss, dur = fetch_segment(spec, raw)
        _overlay_for(clips, title, style, active_pos, png)
        process_clip(src, out_path, png, fill=fill, mute=mute, ss=ss, dur=dur)
    finally:
        for p in (raw, png):
            try:
                p.unlink()
            except OSError:
                pass
    return out_path


def run_pipeline(job_id: str, clips: List[ClipSpec], out_path: Path,
                 title: Sequence[Word] = (), style: Optional[Style] = None,
                 fill: str = "blur", mute: bool = False,
                 progress: Optional[ProgressCB] = None) -> Path:
    """Download, process and stitch every clip. `clips` is already in play order.

    Each clip is composited with the ranking as it stands *at that point in the
    video*, so across the finished cut the list never disappears - it just fills in
    and moves its highlight.
    """
    style = style or Style()

    def report(stage: str, frac: float, done: int):
        if progress:
            progress(stage, frac, done)

    work = settings.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    n = len(clips)
    processed: List[Path] = []

    for i, c in enumerate(clips):
        verb = "Reading" if c.source == "library" else "Downloading"
        report(f"{verb} clip {i + 1}/{n} (#{c.rank})", i / (n + 1), i)
        src, ss, dur = fetch_segment(c, work / f"raw_{i:02d}.mp4")

        report(f"Rendering clip {i + 1}/{n} (#{c.rank})", (i + 0.5) / (n + 1), i)
        png = _overlay_for(clips, title, style, i, work / f"ov_{i:02d}.png")
        out = process_clip(src, work / f"clip_{i:02d}.mp4", png,
                           fill=fill, mute=mute, ss=ss, dur=dur)
        processed.append(out)

    report("Stitching final video", n / (n + 1), n)
    stitch(processed, out_path)
    report("Done", 1.0, n)
    return out_path
