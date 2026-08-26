"""Request/response schemas for the API.

The shape here follows the new "standing ranking" format. The important idea is
that a slot has two independent numbers:

    rank  - where the clip sits in the list that stands on screen all video long
    play position - when the clip actually appears (its index in `clips`)

A countdown video ranks 1..6 top-to-bottom but plays 6 first and 1 last; an
in-order video plays 1 first. Both are just different orderings of the same slots,
which is why `rank` is a field rather than the array index.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class TitleWord(BaseModel):
    """One word of the video title with its own colour, so a title can read
    'Ranking [gold]Insane[/] [red]Parkour[/] Fails' like the reference videos."""
    text: str
    color: str = Field("#FFFFFF", description="Hex colour for this word")


class StyleSpec(BaseModel):
    """Where everything sits, in 1080x1920 output pixels. Defaults reproduce the
    reference layout: title band at the top, ranking list down the left side."""
    title_size: int = Field(78, ge=20, le=200)
    title_top: int = Field(140, ge=0, le=1200)
    title_max_lines: int = Field(2, ge=1, le=4)

    list_x: int = Field(100, ge=0, le=900)
    list_top: int = Field(560, ge=0, le=1700)
    row_gap: int = Field(150, ge=50, le=320)
    number_size: int = Field(84, ge=24, le=220)
    caption_size: int = Field(56, ge=16, le=160)
    caption_color: str = "#FFFFFF"
    number_suffix: Literal[".", "#", ""] = Field(
        ".", description="'.' -> '1.'   '#' -> '#1'   '' -> '1'")
    indent_step: int = Field(4, ge=0, le=40)

    highlight: bool = Field(True, description="Enlarge the row of the clip on screen")
    active_scale: float = Field(1.14, ge=1.0, le=1.6)
    shadow: bool = True

    reveal: Literal["accumulate", "current", "all"] = Field(
        "accumulate",
        description="accumulate: a caption appears when its clip plays and stays for "
                    "the rest of the video. current: only the playing clip's caption "
                    "shows. all: every caption is visible the whole time.",
    )


class Clip(BaseModel):
    """One slot. Array order in `clips` is the PLAY order; `rank` is the row it
    occupies in the standing list, and `caption` is the text revealed next to that
    row when this clip plays.

    A slot's footage comes from one of two places, chosen by `source`:
    `"url"` downloads the window with yt-dlp, `"library"` trims it out of a video
    you already have on disk, addressed by the opaque id from /api/library. The id
    is deliberately not a path - the client never names a filesystem location, so
    nothing outside the configured library roots is reachable.
    """
    source: Literal["url", "library"] = Field(
        "url", description="Where the footage comes from")
    url: str = Field("", description="YouTube, TikTok or Instagram URL (source='url')")
    library_id: str = Field(
        "", description="Id from /api/library of a local file (source='library')")
    start: str = Field(..., description="Segment start: seconds ('12'), 'M:SS' or 'H:MM:SS'")
    end: str = Field(..., description="Segment end, same formats as start")
    rank: int = Field(..., ge=1, le=99, description="Row in the standing ranking list")
    caption: str = Field("", description="Text shown beside the rank when revealed")
    color: str = Field("", description="Hex colour for the rank number; '' = auto")

    @model_validator(mode="after")
    def _one_source(self):
        self.url = (self.url or "").strip()
        self.library_id = (self.library_id or "").strip()
        if self.source == "library":
            if not self.library_id:
                raise ValueError("library_id is required when source is 'library'")
        elif not self.url:
            raise ValueError("url is required when source is 'url'")
        return self


class RankSlot(BaseModel):
    """A ranking row with no video attached - used by /api/overlay so the GUI can
    preview the standing list before any URL has been pasted."""
    rank: int = Field(..., ge=1, le=99)
    caption: str = ""
    color: str = ""


class GenerateRequest(BaseModel):
    clips: List[Clip] = Field(..., min_length=1, max_length=20)
    title: List[TitleWord] = Field(
        default_factory=list,
        description="The video title, word by word so each can have its own colour. "
                    "Drawn identically on every clip, so it stands still across cuts.",
    )
    style: StyleSpec = Field(default_factory=StyleSpec)
    fill: Literal["blur", "crop"] = Field(
        "blur",
        description="How to make each source 9:16. 'blur' letterboxes over a blurred "
        "backdrop (keeps all the action); 'crop' centre-crops to fill (may cut the sides).",
    )
    mute: bool = Field(False, description="Drop the clips' own audio. No music/SFX is ever added.")

    @field_validator("clips")
    @classmethod
    def _clips(cls, v):
        if not v:
            raise ValueError("at least one clip is required")
        return v


class OverlayRequest(BaseModel):
    """Render just the chrome - no video, no download - so the GUI preview is the
    exact PNG that will be burned in, and updates as fast as you can type."""
    slots: List[RankSlot] = Field(default_factory=list, max_length=20)
    title: List[TitleWord] = Field(default_factory=list)
    style: StyleSpec = Field(default_factory=StyleSpec)
    play_order: List[int] = Field(
        default_factory=list,
        description="Indices into `slots`, in play order. Defaults to slot order.",
    )
    active_pos: int = Field(
        0, ge=-1, description="Which play position is on screen. -1 = nothing played yet."
    )


class PreviewRequest(BaseModel):
    """Render ONE real clip with the full standing overlay on it, at the ranking
    state it will have when that clip plays. This is the 'prove it' button next to
    the instant overlay preview - same pipeline, one clip, no stitch."""
    clips: List[Clip] = Field(..., min_length=1, max_length=20,
                              description="Every slot, in play order (needed to know "
                                          "which captions are revealed by then)")
    active_pos: int = Field(0, ge=0, description="Which play position to render")
    title: List[TitleWord] = Field(default_factory=list)
    style: StyleSpec = Field(default_factory=StyleSpec)
    fill: Literal["blur", "crop"] = "blur"
    mute: bool = False


class JobRef(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "error"]
    stage: str = ""
    progress: float = 0.0            # 0..1
    clips_total: int = 0
    clips_done: int = 0
    error: Optional[str] = None
    video_url: Optional[str] = None  # set when completed
