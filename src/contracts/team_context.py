"""Team setting for one game. Missing metrics stay null."""

from typing import Annotated

from pydantic import AfterValidator, Field, model_validator

from contracts.common import (
    UTCDateTime,
    AnalysisState,
    GameId,
    SnapshotId,
    SourceId,
    StrictModel,
    TeamId,
    require_sources,
)


class TeamContext(StrictModel):
    canonical_team_id: TeamId
    canonical_game_id: GameId
    opponent_team_id: TeamId
    is_home: bool
    pace: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    defensive_rating: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    snapshot_id: SnapshotId | None = None
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def distinct_opponent(self) -> "TeamContext":
        if self.canonical_team_id == self.opponent_team_id:
            raise ValueError("opponent must be a different team")
        return self


def metric_state(value: float | None) -> AnalysisState:
    if value is None:
        return AnalysisState.UNAVAILABLE
    return AnalysisState.AVAILABLE
