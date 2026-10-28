"""Validated data models shared across capture, recognition, tracking and the API."""
from __future__ import annotations

import time
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .cards import Card

Game = Literal["blackjack", "holdem"]
RegionKind = Literal["card", "number", "indicator"]


class Rect(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class RegionDef(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    kind: RegionKind
    rect: Rect
    label: str = ""
    # indicators: brightness threshold (0-255) above which the region is "on"
    threshold: int = 110
    # numbers: whether digits are lighter or darker than the background
    polarity: Literal["light", "dark"] = "light"


class Layout(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    game: Game
    width: int
    height: int
    regions: list[RegionDef]
    # sub-boxes inside a card crop, as fractions (x, y, w, h)
    rank_box: tuple[float, float, float, float] = (0.0, 0.0, 0.6, 0.38)
    suit_box: tuple[float, float, float, float] = (0.15, 0.42, 0.7, 0.45)
    synthetic: bool = False

    @field_validator("regions")
    @classmethod
    def unique_ids(cls, v: list[RegionDef]) -> list[RegionDef]:
        ids = [r.id for r in v]
        if len(ids) != len(set(ids)):
            raise ValueError("region ids must be unique")
        return v

    def region(self, rid: str) -> Optional[RegionDef]:
        return next((r for r in self.regions if r.id == rid), None)


ValueSource = Literal["local", "manual", "claude_suggestion"]


class Observation(BaseModel):
    """One recognition result for one region in one frame."""
    region_id: str
    kind: RegionKind
    value: Optional[str]  # card "Ah"|"hidden"|"empty"; number "125"|"empty"; indicator "on"/"off"; None = unreadable
    confidence: float = Field(ge=0, le=1)  # recognition confidence, NOT mathematical probability
    source: ValueSource = "local"
    captured_at: float
    frame_id: int
    detail: str = ""

    @field_validator("value")
    @classmethod
    def check_value(cls, v, info):
        kind = info.data.get("kind")
        if v is None:
            return v
        if kind == "card" and v not in ("hidden", "empty"):
            Card.parse(v)
        if kind == "number" and v != "empty" and not v.replace(".", "", 1).isdigit():
            raise ValueError(f"not a number: {v}")
        if kind == "indicator" and v not in ("on", "off"):
            raise ValueError("indicator must be on/off")
        return v


RegionStatus = Literal["stable", "pending", "uncertain", "manual"]


class RegionState(BaseModel):
    region_id: str
    kind: RegionKind
    value: Optional[str]
    confidence: float
    status: RegionStatus
    source: ValueSource = "local"
    committed_at: float = 0.0
    last_raw: Optional[str] = None
    last_raw_confidence: float = 0.0


class TimelineEvent(BaseModel):
    t: float = Field(default_factory=time.time)
    kind: str
    message: str
    state_version: int = 0
    data: dict = Field(default_factory=dict)


class ManualCorrection(BaseModel):
    region_id: str
    value: Optional[str]  # None clears the correction
