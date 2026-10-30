"""Versioned domain data. Decisions never mutate authoritative facts."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def tile_index(tile: str) -> int:
    if len(tile) != 2 or tile[1] not in "mpsz" or not tile[0].isdigit():
        raise ValueError("invalid tile")
    n, suit = int(tile[0]), tile[1]
    if suit == "z":
        if not 1 <= n <= 7:
            raise ValueError("invalid honor")
        return 27 + n - 1
    if not 0 <= n <= 9:
        raise ValueError("invalid number tile")
    return "mps".index(suit) * 9 + (4 if n == 0 else n - 1)


def tile_name(index: int) -> str:
    if not 0 <= index < 34:
        raise ValueError("invalid tile index")
    return f"{index % 9 + 1}{'mpsz'[index // 9]}"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)


class Phase(StrEnum):
    WAITING = "waiting"
    DISCARD = "awaiting_discard"
    RESPONSE = "response"
    REPLACEMENT = "awaiting_replacement"
    ROUND_END = "round_end"
    ENDED = "ended"
    RECOVERING = "recovering"
    INTERRUPTED = "interrupted"


class Rules(Record):
    players: Literal[4] = 4
    open_tanyao: bool = True
    aka_dora: bool = True
    double_yakuman: bool = True
    composite_yakuman: bool = True
    counted_yakuman: bool = True
    kiriage: bool = False
    starting_points: int | None = None
    mode_id: int | None = None
    room_type: Literal["friend", "ranked", "contest", "unknown"] = "unknown"
    raw: dict[str, Any] = Field(default_factory=dict)
    unsupported: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=lambda: ["room_rules_not_observed"])


class Meld(Record):
    id: str
    actor: int = Field(ge=0, le=3)
    kind: Literal["chi", "pon", "daiminkan", "ankan", "kakan"]
    tiles: list[str]
    source_seat: int | None = Field(default=None, ge=0, le=3)
    called_tile: str | None = None
    consumed_tiles: list[str] = Field(default_factory=list)
    source_event: str | None = None
    event_id: str
    upgraded_from: str | None = None

    @field_validator('tiles','consumed_tiles')
    @classmethod
    def validate_meld_tiles(cls,value):
        for tile in value:
            tile_index(tile)
        return value

    @property
    def closed(self) -> bool:
        return self.kind == "ankan"


class Discard(Record):
    tile: str
    event_id: str
    seq: int
    tsumogiri: bool = False
    riichi: bool = False
    called_by: int | None = None

    @field_validator('tile')
    @classmethod
    def validate_discard_tile(cls,value):
        tile_index(value)
        return value


class Player(Record):
    river: list[Discard] = Field(default_factory=list)
    melds: list[Meld] = Field(default_factory=list)
    riichi: Literal["none", "declared", "established"] = "none"
    riichi_event: str | None = None
    concealed_count: int | None = None


class Operation(Record):
    window_id: str
    state_version: int
    raw: dict[str, Any]
    received_mono: float
    received_at: str
    fixed_ms: int | None = None
    add_ms: int | None = None
    timer_verified: bool = False
    historical: bool = False


class Snapshot(Record):
    schema_version: str = "snapshot.v1"
    match_id: str
    round_uid: str | None = None
    round_seq: int = 0
    state_version: int = 0
    last_event_id: str | None = None
    self_seat: int = Field(ge=0, le=3)
    rules: Rules = Field(default_factory=Rules)
    lifecycle: Literal["active", "ended", "interrupted"] = "active"
    completeness: Literal["full", "partial", "gaps"] = "partial"
    phase: Phase = Phase.WAITING
    wind: int = Field(default=0, ge=0, le=3)
    hand_number: int = Field(default=1, ge=1, le=4)
    honba: int = 0
    deposits: int = 0
    dealer: int = Field(default=0, ge=0, le=3)
    turn: int | None = None
    remaining: int | None = None
    scores: list[int] | None = None
    final_scores: list[int] | None = None
    final_ranks: list[int] | None = None
    hand: list[str] = Field(default_factory=list)
    drawn_index: int | None = None
    dora: list[str] = Field(default_factory=list)
    players: list[Player] = Field(default_factory=lambda: [Player() for _ in range(4)])
    furiten: dict[str, bool | None] = Field(default_factory=lambda: {"server": None, "temporary": None, "riichi": None})
    context: dict[str, Any] = Field(default_factory=dict)
    operation: Operation | None = None
    public_events: list[dict[str, Any]] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    consistent: bool = True
    observed_at: str | None = None

    @field_validator("scores", "final_scores", "final_ranks")
    @classmethod
    def four_values(cls, value):
        if value is not None and len(value) != 4:
            raise ValueError("four values required")
        return value

    @field_validator("players")
    @classmethod
    def four_players(cls, value):
        if len(value) != 4:
            raise ValueError("four players required")
        return value

    @field_validator("hand", "dora")
    @classmethod
    def valid_tiles(cls, value):
        for tile in value:
            tile_index(tile)
        return value

    @property
    def ranks(self) -> list[int] | None:
        if self.scores is None:
            return None
        # Initial seat order is an explicit tie basis, not a claim of final ranking.
        order = sorted(range(4), key=lambda s: (-self.scores[s], s))
        return [order.index(s) + 1 for s in range(4)]


class Event(Record):
    schema_version: str = "event.v1"
    id: str
    match_id: str
    kind: str
    seq: int
    source_step: int | None = None
    data: dict[str, Any]
    received_at: str
    received_mono: float
    historical: bool = False
    source: Literal["websocket", "fixture", "vision"] = "websocket"


class LegalAction(Record):
    id: str
    window_id: str
    state_version: int
    kind: str
    tile_index: int | None = None
    consume_indices: list[int] = Field(default_factory=list)
    meld_id: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    raw_type: int


class Decision(Record):
    q: str
    action_id: str = Field(alias="a")
    reason: str = Field(min_length=1, max_length=240, alias="r")
    plan_update: RoundPlanUpdate | None = Field(alias="p")


class YakuRoute(Record):
    yaku: str = Field(min_length=1, max_length=80,alias="y")
    status: Literal["conditional", "fixed_meld", "established", "target"] = Field(alias="s")
    basis: list[str] = Field(max_length=8,alias="b")
    requirements: list[str] = Field(max_length=8,alias="n")


class SwitchCondition(Record):
    when: str = Field(min_length=1, max_length=240,alias="w")
    then: str = Field(min_length=1, max_length=240,alias="t")


class RoundPlanUpdate(Record):
    based_on_plan_version: int = Field(ge=0,alias="v")
    basis_event_ids: list[str] = Field(min_length=1, max_length=12,alias="e")
    goal: str = Field(min_length=1, max_length=240,alias="g")
    approach: str = Field(min_length=1, max_length=160,alias="a")
    target_yaku: list[YakuRoute] = Field(max_length=8,alias="y")
    priorities: list[str] = Field(max_length=8,alias="p")
    switch_conditions: list[SwitchCondition] = Field(max_length=8,alias="s")
    revision_reason: str = Field(min_length=1, max_length=240,alias="r")


Decision.model_rebuild()


class Receipt(Record):
    id: str
    action_id: str
    window_id: str
    status: Literal["requested", "validated", "mouse_sent", "server_confirmed", "mismatch", "expired", "cancelled", "unknown"]
    created_at: str
    clicked_mono: float | None = None
    actual_event_id: str | None = None
    detail: str | None = None
