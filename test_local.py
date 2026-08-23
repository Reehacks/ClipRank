"""Local smoke test for the ffmpeg stages (no network / no yt-dlp needed).

Builds synthetic sources of assorted aspect ratios, runs process_clip + stitch,
and prints the resulting geometry / streams so we know the pipeline actually works.
"""
import subprocess
from pathlib import Path
from backend import pipeline, settings

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


print("timecode parse:", pipeline.parse_timecode("0:12"), pipeline.parse_timecode("1:02:03"), pipeline.parse_timecode("8.5"))

land = mk("land.mp4", "1280x720", 3)          # 16:9 + audio
port = mk("port.mp4", "720x1280", 3)          # 9:16 + audio
square_noaud = mk("square.mp4", "720x720", 3, audio=False)  # 1:1, NO audio

p5 = pipeline.process_clip(land, T / "p5.mp4", 5, "Insane Speed Course Finale", fill="blur")
p1 = pipeline.process_clip(port, T / "p1.mp4", 1, "Rooftop Escape POV", fill="blur")
p3 = pipeline.process_clip(square_noaud, T / "p3.mp4", 3, "No Audio Clip Test Wrapping Title Long", fill="crop")

for label, p in [("#5 blur/16:9", p5), ("#1 blur/9:16", p1), ("#3 crop/silent", p3)]:
    print(f"{label:16} -> {probe(p)}")

final = pipeline.stitch([p5, p1, p3], T / "final.mp4")  # play order #5,#1,#3
print("FINAL           ->", probe(final))

# grab a frame from #5 to eyeball the burned rank + title
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.0", "-i", str(p5),
                "-frames:v", "1", str(T / "frame_p5.png")], check=True)
print("frame:", (T / "frame_p5.png").resolve())
