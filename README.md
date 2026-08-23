# Ranking Shorts generator

A small full-stack tool that turns a handful of source clips (YouTube / TikTok /
Instagram) into one vertical 9:16 "top N" ranking video. You give each clip a URL, a
start/end time, a rank number and a title; the backend downloads just those windows,
makes each one vertical, burns the rank and title on, and stitches them together in the
order you choose.

By design it **never adds background music or sound effects** — each clip keeps its own
original audio (or none, if you mute it).

This repo currently contains the **backend** (FastAPI + yt-dlp + FFmpeg). The frontend
GUI is the next piece.

## Requirements

- Python 3.10+
- **FFmpeg** and **ffprobe** installed and on your PATH
  (Windows: `winget install Gyan.FFmpeg`, or drop the binaries somewhere and set
  `FFMPEG_BIN` / `FFPROBE_BIN`).
- `yt-dlp` — installed automatically via `requirements.txt`.

## Install & run

```bash
cd ranking-shorts
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

uvicorn backend.app:app --reload --port 8000
```

Then check it's healthy:

```bash
curl http://localhost:8000/api/health
# {"ok":true,"ffmpeg":true,"yt_dlp":true}
```

## API

**Start a render** — returns immediately with a job id. The `clips` array is in play
order (rank is just the burned label, independent of order).

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "fill": "blur",
    "mute": false,
    "clips": [
      {"url":"https://youtu.be/XXXX","start":"0:12","end":"0:20","rank":5,"title":"Speed course"},
      {"url":"https://youtu.be/YYYY","start":"1:40","end":"1:50","rank":1,"title":"Rooftop escape"}
    ]
  }'
# {"job_id":"a1b2c3d4e5f6"}
```

**Poll progress:**

```bash
curl http://localhost:8000/api/jobs/a1b2c3d4e5f6
# {"status":"running","stage":"Rendering clip 1/2 (#5)","progress":0.25, ...}
# ...later: {"status":"completed","video_url":"/api/jobs/a1b2c3d4e5f6/video"}
```

**Download the result:**

```bash
curl -OJ http://localhost:8000/api/jobs/a1b2c3d4e5f6/video
```

## Options

| Field  | Values          | Meaning                                                              |
|--------|-----------------|----------------------------------------------------------------------|
| `fill` | `blur` (default) / `crop` | `blur` letterboxes over a blurred backdrop (keeps all the action); `crop` centre-crops to fill (may cut the sides). |
| `mute` | `false` / `true` | Drop each clip's own audio. Music/SFX are never added either way.    |

## Configuration (environment variables)

| Var | Default | Notes |
|-----|---------|-------|
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Point at explicit executables if not on PATH. |
| `YTDLP_BIN` | auto (PATH) | |
| `RANKING_COOKIES` | – | Path to a Netscape `cookies.txt`. **TikTok, Instagram and age-gated YouTube usually need this.** |
| `RANKING_WIDTH` / `RANKING_HEIGHT` / `RANKING_FPS` | 1080 / 1920 / 30 | Output geometry. |
| `RANKING_CRF` / `RANKING_PRESET` | 20 / veryfast | x264 quality. |
| `RANKING_WORK_DIR` / `RANKING_OUTPUT_DIR` | `./work` / `./output` | Scratch and finished-video folders. |

## How it works (backend/pipeline.py)

1. **`download_segment`** — `yt-dlp --download-sections "*start-end"` grabs only the
   window (not the whole video); falls back to full-download + ffmpeg trim if a
   platform doesn't support sections.
2. **`process_clip`** — one FFmpeg pass: make it 9:16 (`blur` or `crop`), then burn the
   `#rank` badge and wrapped title. Every clip comes out with identical codec params.
3. **`stitch`** — because the clips are identical, the concat demuxer copies streams
   (fast, lossless) in the chosen play order.

## Notes

- TikTok/Instagram links and age-gated YouTube commonly require cookies — export a
  `cookies.txt` and set `RANKING_COOKIES`.
- Job state is in memory; restarting the server forgets past jobs (the mp4s remain in
  `output/`).
