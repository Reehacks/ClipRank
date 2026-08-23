"""Renders the standing ranking overlay as a transparent PNG.

The old build burned per-clip text with FFmpeg's `drawtext`, which meant the rank
and title lived and died with their own clip: the moment a cut happened, the number
vanished and a different one appeared. The reference format works the other way
round - the whole ranking list stands on screen for the entire video, and only its
*state* changes at each cut (which caption is revealed, which row is highlighted).

So instead of drawing text inside FFmpeg, we draw the entire chrome here with
Pillow, once per clip, and hand FFmpeg a finished RGBA image to composite. That buys
three things drawtext could not give us:

  * per-word colours in the title ("Ranking **Insane** **Parkour** Fails"),
  * real colour emoji in the captions,
  * pixel-exact previews - the GUI displays this very same PNG, so what you see
    while editing is literally what gets burned in.

Layout is driven by `Style`, all values in 1080x1920 output pixels.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import settings

# --------------------------------------------------------------------------- #
# fonts
# --------------------------------------------------------------------------- #
# Noto Color Emoji is a CBDT bitmap font: FreeType will *only* open it at its one
# native strike (109px). Anything else raises "invalid pixel size". We therefore
# always rasterise emoji at 109 and scale the bitmap down to the size we need.
EMOJI_NATIVE = 109
_EMOJI_ADVANCE = 135.71875   # advance width of one emoji at 109px
_EMOJI_TOP = -101.0          # glyph top relative to baseline at 109px
_EMOJI_BOTTOM = 27.0         # glyph bottom relative to baseline at 109px
_EMOJI_H = _EMOJI_BOTTOM - _EMOJI_TOP


def _first_existing(*paths) -> Optional[Path]:
    for p in paths:
        if not p:
            continue
        p = Path(p)
        if p.exists():
            return p
    return None


def text_font_path() -> Path:
    """The heavy display face used for the title, numbers and captions."""
    p = _first_existing(
        settings.FONT,
        settings.ASSETS / "Poppins-Bold.ttf",
        settings.ASSETS / "DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    if not p:
        raise RuntimeError("no usable text font found - put a .ttf in assets/")
    return p


def emoji_font_path() -> Optional[Path]:
    """A colour-emoji face, if one is available. Optional: without it, emoji are
    simply skipped rather than rendered as tofu boxes."""
    return _first_existing(
        settings.EMOJI_FONT,
        settings.ASSETS / "NotoColorEmoji.ttf",
        "C:/Windows/Fonts/seguiemj.ttf",
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    )


@lru_cache(maxsize=64)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=1)
def _emoji_font() -> Optional[ImageFont.FreeTypeFont]:
    p = emoji_font_path()
    if not p:
        return None
    # Try the native bitmap strike first, then a plain load for scalable colour
    # fonts such as Segoe UI Emoji (COLR/CPAL), which accept any size.
    for size in (EMOJI_NATIVE, 64):
        try:
            f = ImageFont.truetype(str(p), size)
            f.getbbox("\U0001F600")
            return f
        except Exception:  # noqa: BLE001 - any font failure just disables emoji
            continue
    return None


# --------------------------------------------------------------------------- #
# rich text: a caption is a mix of plain text runs and colour-emoji runs
# --------------------------------------------------------------------------- #
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x1F004, 0x1F0CF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
    (0x2190, 0x21FF), (0x2700, 0x27BF), (0xFE0F, 0xFE0F), (0x1F1E6, 0x1F1FF),
    (0x200D, 0x200D), (0x20E3, 0x20E3), (0x2122, 0x2122), (0x2139, 0x2139),
    (0x2194, 0x21AA), (0x231A, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25FE),
)
_ZERO_WIDTH = {0x200D, 0xFE0F, 0xFE0E}


def _is_emoji_cp(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def segment(text: str) -> List[Tuple[str, str]]:
    """Split a string into [('t', plain), ('e', one emoji cluster), ...].

    Emoji clusters keep ZWJ sequences, variation selectors and skin-tone modifiers
    together so that e.g. a flag or a joined family renders as one glyph.
    """
    runs: List[Tuple[str, str]] = []
    buf, i, n = "", 0, len(text)
    while i < n:
        cp = ord(text[i])
        if _is_emoji_cp(cp) and cp not in _ZERO_WIDTH:
            if buf:
                runs.append(("t", buf))
                buf = ""
            cluster = text[i]
            i += 1
            # swallow modifiers, and ZWJ-joined continuations
            while i < n:
                c = ord(text[i])
                if c in _ZERO_WIDTH or 0x1F3FB <= c <= 0x1F3FF or c == 0x20E3:
                    cluster += text[i]
                    i += 1
                elif cluster and ord(cluster[-1]) == 0x200D and _is_emoji_cp(c):
                    cluster += text[i]
                    i += 1
                else:
                    break
            runs.append(("e", cluster))
        else:
            buf += text[i]
            i += 1
    if buf:
        runs.append(("t", buf))
    return runs


def _emoji_metrics(size: float) -> Tuple[float, float, float, float]:
    """(advance, height, bottom-below-baseline, left-pad) for an emoji at `size`.

    Emoji read as slightly larger than the caps beside them, and need a hair of air
    in front so "No fear" + monkey doesn't collide the way a naive advance would.
    """
    h = size * 0.88
    k = h / _EMOJI_H
    return _EMOJI_ADVANCE * k, h, size * 0.08, size * 0.06


def measure(text: str, size: int, font_path: str) -> float:
    """Advance width of a mixed text/emoji string at `size`."""
    f = _font(font_path, size)
    adv_e, _, _, pad_e = _emoji_metrics(size)
    w = 0.0
    for kind, run in segment(text):
        w += f.getlength(run) if kind == "t" else adv_e + pad_e
    return w


def draw_rich(img: Image.Image, x: float, baseline: float, text: str, *,
              size: int, font_path: str, fill: str,
              stroke_w: int = 0, stroke_fill: str = "#000000") -> float:
    """Draw a mixed text/emoji string with its baseline at `baseline`.

    Returns the x cursor after the last glyph. Emoji are rasterised at the font's
    native strike and scaled, which keeps them crisp at any size.
    """
    d = ImageDraw.Draw(img)
    f = _font(font_path, size)
    ef = _emoji_font()
    adv_e, h_e, drop_e, pad_e = _emoji_metrics(size)

    for kind, run in segment(text):
        if kind == "t":
            d.text((x, baseline), run, font=f, fill=fill, anchor="ls",
                   stroke_width=stroke_w, stroke_fill=stroke_fill)
            x += f.getlength(run)
            continue
        if ef is None:
            continue  # no emoji font: drop the glyph rather than draw tofu
        x += pad_e
        try:
            box = (int(_EMOJI_ADVANCE) + 8, int(_EMOJI_H) + 8)
            tile = Image.new("RGBA", box, (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((4, 4 - _EMOJI_TOP), run, font=ef,
                                      embedded_color=True, anchor="ls")
            scale = h_e / _EMOJI_H
            tile = tile.resize((max(1, round(box[0] * scale)),
                               max(1, round(box[1] * scale))), Image.LANCZOS)
            top = round(baseline + drop_e - h_e - 4 * scale)
            img.alpha_composite(tile, (round(x - 4 * scale), top))
        except Exception:  # noqa: BLE001 - never let one glyph kill a render
            pass
        x += adv_e
    return x


# --------------------------------------------------------------------------- #
# the model the renderer works from
# --------------------------------------------------------------------------- #
@dataclass
class Word:
    """One word of the video title, with its own colour - that's what makes
    'Ranking [yellow]Insane[/] [red]Parkour[/] Fails' possible."""
    text: str
    color: str = "#FFFFFF"


@dataclass
class Row:
    """One line of the standing ranking."""
    rank: int
    caption: str = ""
    color: str = ""          # "" -> auto colour from the rank's position
    revealed: bool = False   # show the caption?
    active: bool = False     # this clip is playing right now


@dataclass
class Style:
    """Everything about where things sit. All values in 1080x1920 pixels, so the
    numbers here are directly comparable to a screenshot of the finished video."""
    title_size: int = 78
    title_top: int = 140
    title_max_lines: int = 2
    title_line_gap: float = 1.12
    title_margin: int = 44

    list_x: int = 100
    list_top: int = 560
    row_gap: int = 150
    number_size: int = 84
    caption_size: int = 56
    caption_gap: int = 16
    caption_color: str = "#FFFFFF"
    number_suffix: str = "."      # "." -> "1."   "#" -> "#1"   "" -> "1"
    indent_step: int = 4          # each row nudged right, like hand-placed text

    highlight: bool = True
    active_scale: float = 1.14

    # Which captions are on screen at a given moment. "accumulate" is the format the
    # reference videos use: a caption appears when its clip plays and stays, so the
    # list visibly fills in over the course of the video.
    reveal: str = "accumulate"    # accumulate | current | all

    stroke: float = 0.085         # outline width as a fraction of font size
    shadow: bool = True
    shadow_blur: int = 9
    shadow_offset: int = 5
    shadow_alpha: int = 150


AUTO_COLORS = {1: "#FFD400", 3: "#FF9500"}
LAST_COLOR = "#FF3B30"
DEFAULT_COLOR = "#FFFFFF"


def auto_color(rank: int, total: int) -> str:
    """The reference videos colour a few positions and leave the rest white:
    gold for the top spot, orange for third, red for the bottom one."""
    if rank in AUTO_COLORS:
        return AUTO_COLORS[rank]
    if rank == total and total > 3:
        return LAST_COLOR
    return DEFAULT_COLOR


# --------------------------------------------------------------------------- #
# layout helpers
# --------------------------------------------------------------------------- #
def _wrap_words(words: Sequence[Word], size: int, max_w: float,
                font_path: str) -> List[List[Word]]:
    """Greedy word wrap that keeps each word's colour attached to it."""
    f = _font(font_path, size)
    space = f.getlength(" ")
    lines: List[List[Word]] = []
    cur: List[Word] = []
    cur_w = 0.0
    for w in words:
        ww = measure(w.text, size, font_path)
        add = ww if not cur else ww + space
        if cur and cur_w + add > max_w:
            lines.append(cur)
            cur, cur_w = [w], ww
        else:
            cur.append(w)
            cur_w += add
    if cur:
        lines.append(cur)
    return lines


def _fit_title(words: Sequence[Word], st: Style, width: int,
               font_path: str) -> Tuple[int, List[List[Word]]]:
    """Shrink the title until it fits `title_max_lines`, down to 62% of the
    requested size. Long titles then stay inside the frame instead of running off
    the edge or eating the ranking's space."""
    max_w = width - 2 * st.title_margin
    size = st.title_size
    while size > st.title_size * 0.62:
        lines = _wrap_words(words, size, max_w, font_path)
        if len(lines) <= st.title_max_lines:
            return size, lines
        size -= 2
    return size, _wrap_words(words, size, max_w, font_path)[: st.title_max_lines]


def parse_title(title: str, colors: Optional[Sequence[str]] = None) -> List[Word]:
    """Turn a plain title string plus a parallel list of colours into Words.
    Missing colours default to white, so a bare string still works."""
    parts = [p for p in re.split(r"\s+", (title or "").strip()) if p]
    out: List[Word] = []
    for i, p in enumerate(parts):
        c = colors[i] if colors and i < len(colors) and colors[i] else "#FFFFFF"
        out.append(Word(p, c))
    return out


# --------------------------------------------------------------------------- #
# the renderer
# --------------------------------------------------------------------------- #
def render_overlay(words: Sequence[Word], rows: Sequence[Row], st: Style,
                   width: int = None, height: int = None) -> Image.Image:
    """Draw the full standing chrome - title band + ranking list - onto a
    transparent RGBA canvas ready to be composited over any clip."""
    W = width or settings.WIDTH
    H = height or settings.HEIGHT
    fp = str(text_font_path())

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # ---- title -----------------------------------------------------------
    if words:
        size, lines = _fit_title(words, st, W, fp)
        stroke = max(2, round(size * st.stroke))
        line_h = size * st.title_line_gap
        # cap height of this face, so `title_top` means "top of the letters"
        baseline = st.title_top + size * 0.72
        for line in lines:
            f = _font(fp, size)
            space = f.getlength(" ")
            widths = [measure(w.text, size, fp) for w in line]
            total = sum(widths) + space * (len(line) - 1)
            x = (W - total) / 2
            for w, ww in zip(line, widths):
                draw_rich(layer, x, baseline, w.text, size=size, font_path=fp,
                          fill=w.color, stroke_w=stroke, stroke_fill="#000000")
                x += ww + space
            baseline += line_h

    # ---- ranking list ----------------------------------------------------
    rows = sorted(rows, key=lambda r: r.rank)
    n = len(rows)
    if n:
        gap = st.row_gap
        cap_h = st.number_size * 0.72
        bottom_limit = H - 380          # keep clear of platform UI at the bottom
        need = st.list_top + cap_h + (n - 1) * gap
        if need > bottom_limit and n > 1:
            gap = max(60, (bottom_limit - st.list_top - cap_h) / (n - 1))

        total_ranks = max((r.rank for r in rows), default=n)
        for i, row in enumerate(rows):
            hot = row.active and st.highlight
            scale = st.active_scale if hot else 1.0
            n_size = max(10, round(st.number_size * scale))
            c_size = max(8, round(st.caption_size * scale))
            stroke_n = max(2, round(n_size * st.stroke))
            stroke_c = max(2, round(c_size * st.stroke))

            color = row.color or auto_color(row.rank, total_ranks)
            if st.number_suffix == "#":
                label = f"#{row.rank}"
            else:
                label = f"{row.rank}{st.number_suffix}"

            x = st.list_x + i * st.indent_step
            baseline = st.list_top + cap_h + i * gap

            end = draw_rich(layer, x, baseline, label, size=n_size, font_path=fp,
                            fill=color, stroke_w=stroke_n, stroke_fill="#000000")

            if row.revealed and row.caption.strip():
                draw_rich(layer, end + st.caption_gap, baseline, row.caption.strip(),
                          size=c_size, font_path=fp, fill=st.caption_color,
                          stroke_w=stroke_c, stroke_fill="#000000")

    if not st.shadow:
        return layer

    # ---- soft drop shadow, taken from the alpha of everything we just drew --
    alpha = layer.getchannel("A").filter(ImageFilter.GaussianBlur(st.shadow_blur))
    alpha = alpha.point(lambda a: min(255, int(a * st.shadow_alpha / 255)))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.putalpha(alpha)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(shadow, (st.shadow_offset, st.shadow_offset))
    out.alpha_composite(layer)
    return out


def render_png(words: Sequence[Word], rows: Sequence[Row], st: Style,
               out_path: Optional[Path] = None) -> bytes | Path:
    """Render and either return PNG bytes (for the GUI preview) or write a file
    (for FFmpeg to composite)."""
    img = render_overlay(words, rows, st)
    if out_path is None:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def rows_for_state(specs: Sequence[Row], play_order: Sequence[int],
                   active_pos: int, reveal: str = "accumulate") -> List[Row]:
    """Build the list's state at one moment in the video.

    `specs` is every slot, `play_order` maps play position -> index into specs, and
    `active_pos` is which position is on screen. `reveal` decides which captions
    are showing:

        accumulate - every clip played so far keeps its caption (the list fills in
                     as the video runs, which is the format in reference photo 1)
        current    - only the clip on screen shows its caption (reference photo 2)
        all        - every caption is visible the whole time
    """
    played = set(play_order[: active_pos + 1]) if active_pos >= 0 else set()
    active_idx = play_order[active_pos] if 0 <= active_pos < len(play_order) else -1
    out: List[Row] = []
    for idx, s in enumerate(specs):
        if reveal == "all":
            revealed = True
        elif reveal == "current":
            revealed = idx == active_idx
        else:
            revealed = idx in played
        out.append(Row(rank=s.rank, caption=s.caption, color=s.color,
                       revealed=revealed, active=idx == active_idx))
    return out
