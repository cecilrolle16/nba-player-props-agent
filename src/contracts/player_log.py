"""Completed-game counting stats. These are observed results, not projections."""

from typing import Annotated

from pydantic import AfterValidator, Field

from contracts.common import (
    UTCDateTime,
    GameId,
    PlayerId,
    SourceId,
    StrictModel,
    TeamId,
    require_sources,
)


class PlayerGameLog(StrictModel):
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    canonical_team_id: TeamId
    points: int = Field(ge=0)
    assists: int = Field(ge=0)
    rebounds: int = Field(ge=0)
    minutes: float = Field(ge=0, allow_inf_nan=False)
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
