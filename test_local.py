"""Local smoke test for the overlay + ffmpeg stages (no network / no yt-dlp needed).

Builds synthetic sources of assorted aspect ratios, renders the standing ranking at
each play position, composites and stitches them, then pulls one frame per clip so
you can eyeball that the list really does stand still and fill in across the cuts.

Run it from the project root:   python test_local.py
"""
import subprocess
from pathlib import Path

from backend import pipeline
from backend.overlay import Row, Style, parse_title, render_png, rows_for_state

T = Path("work/_test")
T.mkdir(parents=True, exist_ok=True)


def mk(name, size, dur, audio=True):
    p = T / name
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(p)]
    subprocess.run(cmd, check=True, capture_output=True)
    return p


def probe(p):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration",
         "-of", "default=noprint_wrappers=1", str(p)],
        capture_output=True, text=True).stdout
    return " ".join(out.split())


print("timecode parse:", pipeline.parse_timecode("0:12"),
      pipeline.parse_timecode("1:02:03"), pipeline.parse_timecode("8.5"))

# --- sources: deliberately mixed aspect ratios, one with no audio at all -------
sources = [
    mk("land.mp4", "1280x720", 3),                      # 16:9 + audio
    mk("port.mp4", "720x1280", 3),                      # 9:16 + audio
    mk("square.mp4", "720x720", 3, audio=False),        # 1:1, NO audio
]

# --- the ranking: three ranks, played as a countdown (3 first, 1 last) ---------
title = parse_title("Ranking Insane Parkour Fails",
                    ["#FFFFFF", "#FFD400", "#FF3B30", "#FFFFFF"])
style = Style()
slots = [Row(rank=1, caption="Rooftop escape \U0001F648"),
         Row(rank=2, caption="No balance \U0001F62E"),
         Row(rank=3, caption="Too heavy \U0001F480")]
play_order = [2, 1, 0]          # play position -> index into slots (rank 3, 2, then 1)

processed = []
for pos, src in enumerate(sources):
    rows = rows_for_state(slots, play_order, pos, style.reveal)
    png = render_png(title, rows, style, T / f"ov_{pos}.png")
    out = pipeline.process_clip(src, T / f"clip_{pos}.mp4", png,
                                fill="blur" if pos < 2 else "crop")
    processed.append(out)
    shown = [f"#{r.rank}" for r in rows if r.revealed]
    print(f"pos {pos} (rank #{slots[play_order[pos]].rank})  captions shown: "
          f"{', '.join(shown) or '-':12} -> {probe(out)}")

final = pipeline.stitch(processed, T / "final.mp4")
print("FINAL ->", probe(final))

# --- one frame per clip, so the standing list can be checked by eye -----------
for i in range(len(processed)):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(i * 3 + 1.0),
                    "-i", str(final), "-frames:v", "1", str(T / f"frame_{i}.png")],
                   check=True)
print("frames:", (T / "frame_0.png").resolve(), "... (one per clip)")
print("\nThe title and all three numbers should be identical in every frame;")
print("only the captions and the enlarged row should differ.")
