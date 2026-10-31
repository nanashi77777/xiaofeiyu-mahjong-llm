"""Explicit, secret-free commands. Navigation is deterministic; play uses the LLM."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, TypeAdapter, field_validator

from .domain import Record


class Command(Record):
    model_config = {**Record.model_config, "frozen": True}
    command_id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")]


class Login(Command):
    kind: Literal["login"]


class Ranked(Command):
    kind: Literal["ranked"]
    tier: Literal["bronze", "silver", "gold", "jade", "throne"]
    length: Literal["east", "south"]

    @field_validator("tier", mode="before")
    @classmethod
    def tier_alias(cls, value):
        return {"铜之间":"bronze", "銅之間":"bronze", "银之间":"silver", "銀之間":"silver",
                "金之间":"gold", "金之間":"gold", "玉之间":"jade", "玉之間":"jade",
                "王座之间":"throne", "王座之間":"throne"}.get(value, value) if isinstance(value, str) else value

    @field_validator("length", mode="before")
    @classmethod
    def length_alias(cls, value):
        return {"四人东":"east", "四人東":"east", "四人南":"south"}.get(value, value) if isinstance(value, str) else value


class JoinFriend(Command):
    kind: Literal["join_friend"]
    # Never coerce a JSON number to text: its leading zeroes are already lost.
    room_code: Annotated[StrictStr, Field(pattern=r"^[0-9]{1,12}$")]
    ready: StrictBool = True


class ThinkingTime(Record):
    model_config = {**Record.model_config, "frozen": True}
    # Contract bounds only. The actual client must support the exact pair;
    # these bounds are not a claim about the UI's supported values.
    fixed_seconds: Annotated[StrictInt, Field(ge=1, le=3600)]
    bank_seconds: Annotated[StrictInt, Field(ge=0, le=3600)]


class CreateFriend(Command):
    kind: Literal["create_friend"]
    players: Literal[4]
    # The current four-player client exposes AI beside the three lengths.
    # It is a distinct client mode, not an independently combinable opponent.
    length: Literal["single", "east", "south", "ai"]
    thinking: ThinkingTime


LobbyCommand = Annotated[Login | Ranked | JoinFriend | CreateFriend, Field(discriminator="kind")]
COMMANDS = TypeAdapter(LobbyCommand)


def parse_command(data) -> Login | Ranked | JoinFriend | CreateFriend:
    return COMMANDS.validate_python(data)
