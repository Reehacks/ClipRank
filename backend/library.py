"""The local clip library: your own rendered videos, offered beside the URL box.

A library item is addressed by an opaque `id`, not by a path. The id is
`<root index>/<path relative to that root>`, base64url-encoded, and every lookup
re-resolves it against the roots and refuses anything that lands outside them -
so a crafted id cannot read `../../../etc/passwd`. Encoding also keeps ids free of
the brackets, spaces and hashes that real clip filenames are full of, which would
otherwise need escaping in a URL.

Listing is cheap except for the ffprobe per file, so durations are memoised on
(path, size, mtime): a file that has not changed is probed once per process.
Poster frames are cached on disk under settings.THUMB_DIR for the same reason.
"""
from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import settings


class LibraryError(RuntimeError):
    """Raised with a human-readable message when an item cannot be resolved."""


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #
def _encode(root_index: int, rel: str) -> str:
    raw = f"{root_index}/{rel}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(item_id: str) -> Tuple[int, str]:
    pad = "=" * (-len(item_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(item_id + pad).decode("utf-8")
        idx, rel = raw.split("/", 1)
        return int(idx), rel
    except Exception as exc:  # noqa: BLE001 - any malformed id is one error
        raise LibraryError(f"bad library id: {item_id!r}") from exc


def roots() -> List[Path]:
    """Configured library roots, in order. Missing folders are kept in the list so
    the index of an existing root never shifts when a sibling is created later -
    an id minted today must still resolve tomorrow."""
    return [Path(p) for p in settings.LIBRARY_DIRS]


def resolve(item_id: str) -> Path:
    """Turn a library id back into a real file, or raise."""
    idx, rel = _decode(item_id)
    rs = roots()
    if not (0 <= idx < len(rs)):
        raise LibraryError("library id points at a folder that is not configured")
    root = rs[idx].resolve()
    path = (root / rel).resolve()
    if root not in path.parents and path != root:
        raise LibraryError("library id resolves outside its folder")
    if not path.is_file():
        raise LibraryError(f"no such file in the library: {rel}")
    return path


# --------------------------------------------------------------------------- #
# probing, memoised on (path, size, mtime)
# --------------------------------------------------------------------------- #
_PROBE_CACHE: Dict[Tuple[str, int, int], Tuple[float, int, int]] = {}


def probe(path: Path) -> Tuple[float, int, int]:
    """Return (duration seconds, width, height). Zeros if ffprobe cannot say.

    The VIDEO stream's duration is read, not the container's: a file carrying a
    long data or subtitle stream reports a container duration that is not how much
    picture there is, and a start/end picked from that number runs off the end.
    """
    st = path.stat()
    key = (str(path), st.st_size, int(st.st_mtime))
    hit = _PROBE_CACHE.get(key)
    if hit:
        return hit
    dur, w, h = 0.0, 0, 0
    try:
        # JSON, not `default=nk=1`: the flat form prints the fields in the stream's
        # own declaration order, not the order asked for, so positional parsing
        # silently swaps width and duration on some containers.
        out = subprocess.run(
            [settings.FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration,width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout
        data = json.loads(out or "{}")
        stream = (data.get("streams") or [{}])[0]
        w = int(float(stream.get("width") or 0))
        h = int(float(stream.get("height") or 0))
        raw = stream.get("duration") or (data.get("format") or {}).get("duration")
        dur = float(raw) if raw not in (None, "", "N/A") else 0.0
    except Exception:  # noqa: BLE001 - an unprobeable file is still listable
        pass
    _PROBE_CACHE[key] = (dur, w, h)
    return dur, w, h


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    id: str
    name: str          # filename
    rel: str           # path relative to its root, posix
    folder: str        # containing folder relative to the root ('' = root itself)
    root: str          # the root's own display path
    size: int
    mtime: float
    duration: float
    width: int
    height: int


def list_items(probe_media: bool = True) -> List[Item]:
    """Every video under every configured root, newest first.

    Newest first because the clip you just rendered is overwhelmingly the one you
    are about to rank.
    """
    items: List[Item] = []
    for idx, root in enumerate(roots()):
        if not root.is_dir():
            continue
        rroot = root.resolve()
        for path in sorted(rroot.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in settings.LIBRARY_EXTS:
                continue
            rel = path.relative_to(rroot).as_posix()
            folder = str(Path(rel).parent.as_posix())
            dur, w, h = probe(path) if probe_media else (0.0, 0, 0)
            items.append(Item(
                id=_encode(idx, rel),
                name=path.name,
                rel=rel,
                folder="" if folder == "." else folder,
                root=str(root),
                size=path.stat().st_size,
                mtime=path.stat().st_mtime,
                duration=round(dur, 3),
                width=w,
                height=h,
            ))
    items.sort(key=lambda i: i.mtime, reverse=True)
    return items


def as_dicts(items: List[Item]) -> List[dict]:
    return [asdict(i) for i in items]


# --------------------------------------------------------------------------- #
# poster frames
# --------------------------------------------------------------------------- #
def thumbnail(item_id: str) -> Optional[Path]:
    """A cached 320px-wide JPEG poster for one item, or None if it cannot be made.

    The frame is taken a third of the way in rather than at 0: the first frame of a
    rendered clip is very often a fade from black, which makes a whole gallery of
    black squares.
    """
    src = resolve(item_id)
    st = src.stat()
    out = settings.THUMB_DIR / f"{item_id[:64]}_{int(st.st_mtime)}.jpg"
    if out.exists():
        return out
    dur, _, _ = probe(src)
    at = max(0.0, dur / 3.0) if dur else 0.0
    settings.THUMB_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [settings.FFMPEG, "-y", "-ss", str(at), "-i", str(src), "-frames:v", "1",
         "-vf", "scale=320:-2", "-q:v", "5", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out.exists():
        return None
    return out
