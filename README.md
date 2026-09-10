# ClipRank

Make a vertical 9:16 "top N" ranking video from a handful of source clips.

The whole ranking list **stands on screen for the entire video** and fills in as each
clip plays — instead of each clip carrying its own number that vanishes at the cut.

You give each rank a caption, a source and a start/end time, and say when it plays. A
source is either a **link** (YouTube / TikTok / Instagram) or **one of your own videos**
already on disk, picked from a thumbnail gallery. ClipRank takes just those windows,
makes each one vertical, draws the ranking overlay in the state it should have at that
point in the video, and stitches everything together.

By design it **never adds background music or sound effects** — each clip keeps its own
original audio (or none, if you mute it).

There is a browser GUI (one live 9:16 preview of the whole video, 3–10 ranks, per-word
title colours, layout sliders) served by a Python backend (FastAPI + Pillow + yt-dlp +
FFmpeg). Everything runs on your own machine. No account, no API key, no upload.

## The format

```
        Ranking  Insane  Parkour  Fails      <- title, per-word colours, never moves

   1.                                        <- the list stands for the whole video
   2.  No fear 🙈                            <- captions appear as their clip plays
   3.  Felt that 😮                             and (by default) stay revealed
   4.  No balance 🙈
   5.  Too heavy 💀
   6.  Almost 😂
```

Two numbers per clip, kept deliberately separate:

- **rank** — which line of the standing list the clip owns (1 at the top).
- **play position** — when the clip actually appears.

A countdown ranks 1..6 top-to-bottom but plays 6 first and 1 last. In the GUI you set
rank by which table row you type in, and play position in the **Plays** column (or hit
the *Countdown* / *In order* presets).

**Caption behaviour** is a setting:

| Mode | What you see |
|------|--------------|
| `accumulate` *(default)* | A caption appears when its clip plays and stays for the rest of the video — the list visibly fills in. |
| `current` | Only the clip on screen shows its caption. |
| `all` | Every caption is visible the whole time. |

## Install

You need two things installed once, before anything else:

- **Python 3.10 or newer** — <https://www.python.org/downloads/> (on Windows tick
  *Add Python to PATH* in the installer).
- **FFmpeg** (this also gives you `ffprobe`):
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`

Then get ClipRank:

```bash
git clone https://github.com/Reehacks/ClipRank.git
cd ClipRank
```

## Run it (easy way)

- **Windows** — double-click **`start.bat`**.
- **macOS / Linux** — run **`./start.sh`**.

The first run builds a virtual environment and installs the Python packages; every run
after that is instant. Your browser opens at <http://localhost:8000> automatically.

Type a title, fill in the ranks, hit **Generate video**. The finished clip appears in
the page to preview and download. Close the black console window to stop the server.

## Run it (manual way)

```bash
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
uvicorn backend.app:app --port 8000
```

Check it is healthy:

```bash
curl http://localhost:8000/api/health
# {"ok":true,"ffmpeg":true,"yt_dlp":true,"font":"Poppins-Bold.ttf","emoji_font":"NotoColorEmoji.ttf"}
```

If `"ffmpeg":false` comes back, FFmpeg is not on your PATH — see *Troubleshooting*.

## The preview

The single 9:16 pane on the left is not a mock-up. It POSTs to `/api/overlay`, which
runs the *same* renderer the final burn-in uses and returns the exact PNG that FFmpeg
will composite — so nothing can drift between what you design and what comes out.

- The **scrubber** under it picks which play position you are looking at, so you can
  step through the ranking filling in.
- **▶ Play through** cycles the positions automatically.
- **🎬 Render this clip for real** downloads that one clip and burns the overlay onto
  the real footage — the slow, accurate check.

## Your own clips (the local library)

Beside the URL box, every row has a **File** option that lists videos the backend can
already see on disk, with poster frames, a folder filter and a scrub-and-pick preview.
Useful when a clip was made by something else and was never on the internet.

The default folder is `%USERPROFILE%\Videos\Edit Ranking videos` on Windows
(`~/Videos/Edit Ranking videos` elsewhere).

Override it with `RANKING_LIBRARY_DIRS` — several folders separated by `;` on Windows
or `:` elsewhere:

```bash
set RANKING_LIBRARY_DIRS=D:\renders;C:\Users\me\Videos\clips
```

Each folder is walked recursively; `.mp4 .mov .mkv .webm .m4v .avi` are listed, newest
first.

Two things worth knowing:

- **A clip is addressed by an opaque id, never by a path.** The browser never sends a
  filesystem location and the backend refuses one, so nothing outside the configured
  folders is reachable even with a hand-crafted request.
- **A local clip is trimmed inside the single normalise pass**, not pre-cut to an
  intermediate file. A downloaded clip arrives already cut by yt-dlp; a local one is
  already on disk, so cutting it first would encode the picture twice for nothing.

## Options

| Field  | Values          | Meaning                                                              |
|--------|-----------------|----------------------------------------------------------------------|
| `fill` | `blur` (default) / `crop` | `blur` letterboxes over a blurred backdrop (keeps all the action); `crop` centre-crops to fill (may cut the sides). |
| `mute` | `false` / `true` | Drop each clip's own audio. Music/SFX are never added either way.    |
| `title` | list of `{text,color}` | The whole-video title, word by word so each word can have its own colour. |
| `style.reveal` | `accumulate` / `current` / `all` | See the table above. |
| `style.number_suffix` | `.` / `#` / `""` | `1.` · `#1` · `1` |
| `style.highlight` | bool | Enlarge the row whose clip is currently on screen. |

Every geometry value in `style` (`title_size`, `title_top`, `list_x`, `list_top`,
`row_gap`, `number_size`, `caption_size`, `indent_step`, …) is in **1080×1920 output
pixels**, so the numbers match a screenshot of the finished video one-to-one. The GUI's
*Layout* sliders write straight into these.

## Configuration (environment variables)

| Var | Default | Notes |
|-----|---------|-------|
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Point at explicit executables if not on PATH. |
| `YTDLP_BIN` | auto (PATH) | |
| `RANKING_FONT` | `assets/Poppins-Bold.ttf` | Display face. |
| `RANKING_EMOJI_FONT` | `assets/NotoColorEmoji.ttf` | Colour emoji face; optional. |
| `RANKING_COOKIES` | – | Path to a Netscape `cookies.txt`. **TikTok, Instagram and age-gated YouTube usually need this.** |
| `RANKING_LIBRARY_DIRS` | `~/Videos/Edit Ranking videos` | Folders the local clip picker may read. |
| `RANKING_WIDTH` / `RANKING_HEIGHT` / `RANKING_FPS` | 1080 / 1920 / 30 | Output geometry. |
| `RANKING_CRF` / `RANKING_PRESET` | 20 / veryfast | x264 quality. |
| `RANKING_WORK_DIR` / `RANKING_OUTPUT_DIR` | `./work` / `./output` | Scratch and finished-video folders. |

## API

The GUI is just a client for this. Everything below is on `http://localhost:8000`.

**Start a render** — returns immediately with a job id. `clips` is in **play order**;
`rank` is the row each clip owns in the standing list.

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "fill": "blur",
    "mute": false,
    "title": [
      {"text":"Ranking","color":"#FFFFFF"},
      {"text":"Insane","color":"#FFD400"},
      {"text":"Parkour","color":"#FF3B30"},
      {"text":"Fails","color":"#FFFFFF"}
    ],
    "style": {"reveal":"accumulate","number_suffix":"."},
    "clips": [
      {"url":"https://youtu.be/XXXX","start":"0:12","end":"0:20","rank":6,"caption":"Almost 😂"},
      {"source":"library","library_id":"MC9jbGlwLm1wNA","start":"0:03","end":"0:11",
       "rank":5,"caption":"Too heavy 💀"}
    ]
  }'
# {"job_id":"a1b2c3d4e5f6"}
```

**Render the overlay only** — instant, no download, returns a PNG. This is what the GUI
preview shows:

```bash
curl -X POST http://localhost:8000/api/overlay -o overlay.png \
  -H "Content-Type: application/json" \
  -d '{"slots":[{"rank":1,"caption":""},{"rank":2,"caption":"No fear 🙈"}],
       "title":[{"text":"Ranking","color":"#FFFFFF"},{"text":"Fails","color":"#FF3B30"}],
       "play_order":[1,0], "active_pos":0}'
```

**Render one real clip** — the whole slot list (so the renderer knows which captions are
revealed by then) plus which play position to render:

```bash
curl -X POST http://localhost:8000/api/preview \
  -H "Content-Type: application/json" \
  -d '{"clips":[{"url":"https://youtu.be/XXXX","start":"0:12","end":"0:20","rank":6,
                 "caption":"Almost 😂"}], "active_pos":0}'
```

**Poll progress:**

```bash
curl http://localhost:8000/api/jobs/a1b2c3d4e5f6
# {"status":"running","stage":"Rendering clip 1/6 (#6)","progress":0.25, ...}
# ...later: {"status":"completed","video_url":"/api/jobs/a1b2c3d4e5f6/video"}
```

**Download the result:**

```bash
curl -OJ http://localhost:8000/api/jobs/a1b2c3d4e5f6/video
```

Other endpoints: `GET /api/health`, `GET /api/library`,
`GET /api/library/file/{id}`, `GET /api/library/thumb/{id}`.

## How it works

1. **`backend/overlay.py`** — Pillow draws the whole chrome (title band + ranking list)
   onto a transparent 1080×1920 RGBA image: per-word title colours, per-rank number
   colours, colour emoji rasterised at Noto's native strike and scaled, a soft drop
   shadow. `rows_for_state()` decides which captions are revealed and which row is
   highlighted at a given play position.
2. **`pipeline.download_segment`** — `yt-dlp --download-sections "*start-end"` grabs only
   the window, not the whole video; falls back to full-download + FFmpeg trim if a
   platform does not support sections.
3. **`pipeline.process_clip`** — one FFmpeg pass: make it 9:16 (`blur` or `crop`), then
   composite that clip's overlay PNG over the top. Because it is a still image, FFmpeg
   holds it for the clip's full duration, so the list is rock steady.
4. **`pipeline.stitch`** — every clip comes out with identical codec params, so the
   concat demuxer copies streams (fast, lossless) in play order. Across the joins the
   ranking looks like one continuous element that simply updates at each cut.

Drawing the text in Pillow rather than FFmpeg's `drawtext` is what makes per-word
colours, colour emoji and a pixel-exact GUI preview possible at all.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `"ffmpeg":false` from `/api/health` | FFmpeg is not on PATH. Install it, reopen the terminal, or set `FFMPEG_BIN` and `FFPROBE_BIN` to the full `.exe` paths. |
| A TikTok / Instagram / age-gated YouTube link fails to download | Export a Netscape `cookies.txt` from a logged-in browser and set `RANKING_COOKIES` to it. |
| A YouTube link that used to work suddenly fails | `pip install -U yt-dlp` — sites change and yt-dlp ships fixes constantly. |
| Emoji render as boxes | `assets/NotoColorEmoji.ttf` is missing or `RANKING_EMOJI_FONT` points somewhere wrong. |
| Past jobs vanished after a restart | Job state is in memory by design. The finished mp4s are still in `output/`. |
| The **File** picker is empty | Nothing in `~/Videos/Edit Ranking videos`. Set `RANKING_LIBRARY_DIRS` to your own folder. |

## Licence and fonts

The code is MIT. The bundled fonts are their own:

- **Poppins-Bold** and **NotoColorEmoji** — SIL Open Font License 1.1.
- **DejaVuSans-Bold** — DejaVu Fonts License (free).

You are responsible for having the right to use the footage you download. ClipRank does
not host, provide or check any source material.
