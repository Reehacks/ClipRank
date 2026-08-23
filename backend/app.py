"""FastAPI app for the Ranking Shorts generator.

Endpoints
    POST /api/overlay          -> image/png   the standing ranking, rendered instantly
    POST /api/generate         -> {job_id}    start a full render (returns immediately)
    POST /api/preview          -> {job_id}    render ONE real clip, overlay and all
    GET  /api/jobs/{job_id}     -> JobStatus    poll progress (shared by both above)
    GET  /api/jobs/{job_id}/video               download the finished mp4
    GET  /api/health           -> {ok, ffmpeg, yt_dlp, fonts}

/api/overlay is the one that makes the GUI feel live: it draws the exact PNG that
gets burned into the video, but skips every download and encode, so the preview can
be re-rendered on every keystroke. /api/preview is the slower "prove it" path that
actually fetches a clip and burns the overlay onto real footage.

Rendering runs in a background thread so long jobs never block the request. Job state
lives in memory (fine for a local single-user tool); swap the JOBS dict for Redis/DB
if you ever run this multi-user.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from pathlib import Path
from typing import Dict, List, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import settings
from .models import (GenerateRequest, JobRef, JobStatus, OverlayRequest,
                     PreviewRequest, StyleSpec, TitleWord)
from .overlay import Row, Style, Word, emoji_font_path, render_png, rows_for_state
from .pipeline import ClipSpec, PipelineError, preview_clip, run_pipeline

app = FastAPI(title="Ranking Shorts", version="0.2.0")

# Allow the frontend (served here, or from a dev server on another port) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache(request, call_next):
    """StaticFiles sends no Cache-Control header, so browsers fall back to a
    heuristic and can keep serving an old index.html for a long time even after the
    file on disk changes - confusing during active development. This is a local
    single-user tool, so just tell the browser never to cache anything."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


JOBS: Dict[str, JobStatus] = {}
_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# schema -> renderer translation
# --------------------------------------------------------------------------- #
def _style(spec: StyleSpec) -> Style:
    return Style(**spec.model_dump())


def _title(words: Sequence[TitleWord]) -> List[Word]:
    return [Word(w.text, w.color or "#FFFFFF") for w in words]


def _specs(clips) -> List[ClipSpec]:
    return [ClipSpec(c.url, c.start, c.end, c.rank, c.caption, c.color) for c in clips]


def _set(job_id: str, **fields):
    with _LOCK:
        job = JOBS[job_id]
        for k, v in fields.items():
            setattr(job, k, v)


# --------------------------------------------------------------------------- #
# workers
# --------------------------------------------------------------------------- #
def _worker(job_id: str, req: GenerateRequest):
    _set(job_id, status="running", stage="Starting", clips_total=len(req.clips))
    out = settings.OUTPUT_DIR / f"{job_id}.mp4"

    def progress(stage: str, frac: float, done: int):
        _set(job_id, stage=stage, progress=round(frac, 3), clips_done=done)

    try:
        run_pipeline(job_id, _specs(req.clips), out, title=_title(req.title),
                     style=_style(req.style), fill=req.fill, mute=req.mute,
                     progress=progress)
        _set(job_id, status="completed", stage="Done", progress=1.0,
             clips_done=len(req.clips), video_url=f"/api/jobs/{job_id}/video")
    except PipelineError as e:
        _set(job_id, status="error", error=str(e))
    except Exception:  # noqa: BLE001 - surface anything unexpected to the client
        _set(job_id, status="error", error=traceback.format_exc(limit=3))


def _worker_preview(job_id: str, req: PreviewRequest):
    _set(job_id, status="running", stage="Downloading & rendering preview", progress=0.1)
    out = settings.OUTPUT_DIR / f"{job_id}.mp4"
    try:
        preview_clip(_specs(req.clips), req.active_pos, out,
                     title=_title(req.title), style=_style(req.style),
                     fill=req.fill, mute=req.mute,
                     tmp_dir=settings.WORK_DIR / job_id)
        _set(job_id, status="completed", stage="Done", progress=1.0,
             clips_done=1, video_url=f"/api/jobs/{job_id}/video")
    except PipelineError as e:
        _set(job_id, status="error", error=str(e))
    except Exception:  # noqa: BLE001
        _set(job_id, status="error", error=traceback.format_exc(limit=3))


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    import shutil
    from .overlay import text_font_path
    try:
        font = str(text_font_path().name)
    except Exception:  # noqa: BLE001
        font = ""
    emoji = emoji_font_path()
    return {
        "ok": True,
        "ffmpeg": bool(shutil.which(settings.FFMPEG) or Path(settings.FFMPEG).exists()),
        "yt_dlp": bool(shutil.which(settings.YTDLP) or Path(settings.YTDLP).exists()),
        "font": font,
        "emoji_font": emoji.name if emoji else "",
    }


@app.post("/api/overlay")
def overlay(req: OverlayRequest):
    """Render the standing ranking as a transparent PNG - no video involved.

    This is what the GUI's single preview shows. Because it is the very same code
    the burn-in uses, the preview is not an approximation: it is the finished pixels.
    """
    order = req.play_order or list(range(len(req.slots)))
    order = [i for i in order if 0 <= i < len(req.slots)]
    rows = rows_for_state(
        [Row(rank=s.rank, caption=s.caption, color=s.color) for s in req.slots],
        order, req.active_pos, req.style.reveal,
    )
    png = render_png(_title(req.title), rows, _style(req.style))
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/generate", response_model=JobRef)
def generate(req: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        JOBS[job_id] = JobStatus(job_id=job_id, status="queued",
                                 clips_total=len(req.clips))
    threading.Thread(target=_worker, args=(job_id, req), daemon=True).start()
    return JobRef(job_id=job_id)


@app.post("/api/preview", response_model=JobRef)
def preview(req: PreviewRequest):
    """Render a single real clip with the overlay in the state it will have at that
    point in the video - the accurate-but-slow counterpart to /api/overlay."""
    if req.active_pos >= len(req.clips):
        raise HTTPException(400, "active_pos is past the end of clips")
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        JOBS[job_id] = JobStatus(job_id=job_id, status="queued", clips_total=1)
    threading.Thread(target=_worker_preview, args=(job_id, req), daemon=True).start()
    return JobRef(job_id=job_id)


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    if job.status != "completed":
        raise HTTPException(409, f"job is {job.status}, not completed")
    path = settings.OUTPUT_DIR / f"{job_id}.mp4"
    if not path.exists():
        raise HTTPException(404, "output missing")
    return FileResponse(path, media_type="video/mp4", filename=f"ranking_{job_id}.mp4")


# Serve the frontend from ../frontend at the site root, if present.
_frontend = Path(__file__).resolve().parent.parent / "frontend"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
