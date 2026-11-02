"""Stored projection inputs. The baseline calculator lives in projection.baseline."""

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


class ExpectedMinutesInput(StrictModel):
    """Explicit future minutes. This is not the historical average."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    known: bool = False
    expected_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    minutes_uncertainty: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    snapshot_id: SnapshotId | None = None
    available_at: UTCDateTime | None = None
    source_ids: tuple[SourceId, ...] = ()


class RatePrior(StrictModel):
    """A provenance-backed prior rate. The calculator does not invent one."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    prior_rate: float = Field(ge=0, allow_inf_nan=False)
    prior_minutes: float = Field(gt=0, allow_inf_nan=False)
    snapshot_id: SnapshotId
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
