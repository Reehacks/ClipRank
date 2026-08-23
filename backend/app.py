"""FastAPI app for the Ranking Shorts generator.

Endpoints
    POST /api/generate         -> {job_id}       start a render (returns immediately)
    GET  /api/jobs/{job_id}     -> JobStatus       poll progress
    GET  /api/jobs/{job_id}/video                  download the finished mp4
    GET  /api/health           -> {ok, ffmpeg, yt_dlp}

Rendering runs in a background thread so long jobs never block the request. Job state
lives in memory (fine for a local single-user tool); swap the JOBS dict for Redis/DB
if you ever run this multi-user.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import settings
from .models import GenerateRequest, JobRef, JobStatus
from .pipeline import ClipSpec, PipelineError, run_pipeline

app = FastAPI(title="Ranking Shorts", version="0.1.0")

# Allow the frontend (served here, or from a dev server on another port) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: Dict[str, JobStatus] = {}
_LOCK = threading.Lock()


def _set(job_id: str, **fields):
    with _LOCK:
        job = JOBS[job_id]
        for k, v in fields.items():
            setattr(job, k, v)


def _worker(job_id: str, req: GenerateRequest):
    _set(job_id, status="running", stage="Starting", clips_total=len(req.clips))
    out = settings.OUTPUT_DIR / f"{job_id}.mp4"

    def progress(stage: str, frac: float, done: int):
        _set(job_id, stage=stage, progress=round(frac, 3), clips_done=done)

    specs = [ClipSpec(c.url, c.start, c.end, c.rank, c.title) for c in req.clips]
    try:
        run_pipeline(job_id, specs, out, fill=req.fill, mute=req.mute, progress=progress)
        _set(job_id, status="completed", stage="Done", progress=1.0,
             clips_done=len(specs), video_url=f"/api/jobs/{job_id}/video")
    except PipelineError as e:
        _set(job_id, status="error", error=str(e))
    except Exception:  # noqa: BLE001 - surface anything unexpected to the client
        _set(job_id, status="error", error=traceback.format_exc(limit=3))


@app.get("/api/health")
def health():
    import shutil
    return {
        "ok": True,
        "ffmpeg": bool(shutil.which(settings.FFMPEG) or Path(settings.FFMPEG).exists()),
        "yt_dlp": bool(shutil.which(settings.YTDLP) or Path(settings.YTDLP).exists()),
    }


@app.post("/api/generate", response_model=JobRef)
def generate(req: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        JOBS[job_id] = JobStatus(job_id=job_id, status="queued",
                                 clips_total=len(req.clips))
    threading.Thread(target=_worker, args=(job_id, req), daemon=True).start()
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


# Serve the frontend (built later) from ../frontend at the site root, if present.
_frontend = Path(__file__).resolve().parent.parent / "frontend"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
