"""Published game identity."""

from typing import Annotated

from pydantic import AfterValidator, Field, model_validator

from contracts.common import (
    UTCDateTime,
    GameId,
    GameStatus,
    SourceId,
    StrictModel,
    TeamId,
    require_sources,
)


class ScheduleGame(StrictModel):
    canonical_game_id: GameId
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    home_team_id: TeamId
    away_team_id: TeamId
    status: GameStatus
    tip_at: UTCDateTime
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def distinct_teams(self) -> "ScheduleGame":
        if self.home_team_id == self.away_team_id:
            raise ValueError("home and away teams must differ")
        return self
