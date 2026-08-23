"""Request/response schemas for the API."""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator


class Clip(BaseModel):
    """One slot in the ranking. The play order is the order clips arrive in the
    request (the frontend's drag-and-drop produces that order); `rank` is only the
    number burned on screen and is independent of play order."""
    url: str = Field(..., description="YouTube, TikTok or Instagram URL")
    start: str = Field(..., description="Segment start: seconds ('12'), 'M:SS' or 'H:MM:SS'")
    end: str = Field(..., description="Segment end, same formats as start")
    rank: int = Field(..., ge=1, le=99, description="Rank number burned on the clip, e.g. 1..5")
    title: str = Field("", description="Custom title text burned onto the clip")

    @field_validator("url")
    @classmethod
    def _url_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("url is required")
        return v.strip()


class GenerateRequest(BaseModel):
    clips: List[Clip] = Field(..., min_length=1, max_length=20)
    fill: Literal["blur", "crop"] = Field(
        "blur",
        description="How to make each source 9:16. 'blur' letterboxes over a blurred "
        "backdrop (keeps all the action); 'crop' centre-crops to fill (may cut the sides).",
    )
    mute: bool = Field(False, description="Drop the clips' own audio. No music/SFX is ever added.")

    @field_validator("clips")
    @classmethod
    def _ranks(cls, v):
        if not v:
            raise ValueError("at least one clip is required")
        return v


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
