"""Stored projection record. This step does not calculate projections."""

from typing import Annotated

from pydantic import AfterValidator, Field

from contracts.common import (
    UTCDateTime,
    GameId,
    Market,
    PlayerId,
    ProjectionId,
    SnapshotId,
    SourceId,
    StrictModel,
    require_sources,
)


class Projection(StrictModel):
    canonical_projection_id: ProjectionId
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    mean: float | None = Field(default=None, allow_inf_nan=False)
    is_historical: bool
    model_version: str = Field(min_length=1)
    rules_version: str = Field(min_length=1)
    snapshot_id: SnapshotId | None = None
    projected_at: UTCDateTime
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
