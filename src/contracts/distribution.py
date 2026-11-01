"""Stat distribution for one player, game, and market. No line and no side.

A later calculator may fill `outcomes`. This module does not. Win, loss, and
push probabilities belong on `MarketComparison` because they depend on a line
and a side.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from contracts.common import (
    UTCDateTime,
    FeatureId,
    GameId,
    Market,
    PlayerId,
    ProjectionId,
    SourceId,
    StrictModel,
    require_sources,
)
from contracts.stages import CalibrationStatus, StageState, ValueOrigin


class StatMass(StrictModel):
    """One point on the counting-stat distribution. `stat` is points, assists, or rebounds."""

    stat: int = Field(ge=0)
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)


class ProjectionDistribution(StrictModel):
    canonical_projection_id: ProjectionId
    feature_id: FeatureId
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    outcomes: tuple[StatMass, ...] = ()
    mean: float | None = Field(default=None, allow_inf_nan=False)
    rules_version: str = Field(min_length=1)
    model_version: Literal["none"]
    stage_state: StageState
    origin: ValueOrigin
    calibration_status: CalibrationStatus
    projected_at: UTCDateTime | None = None
    available_at: UTCDateTime | None = None
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def labels_and_mass(self) -> "ProjectionDistribution":
        stats = [item.stat for item in self.outcomes]
        if len(stats) != len(set(stats)):
            raise ValueError("stat outcomes must be unique")
        if self.outcomes and abs(sum(item.probability for item in self.outcomes) - 1) > 1e-9:
            raise ValueError("stat outcome probabilities must sum to 1")
        filled = self.mean is not None or bool(self.outcomes)
        if filled and (self.projected_at is None or self.available_at is None):
            raise ValueError("a stored stat distribution keeps projected_at and available_at")
        if self.stage_state is StageState.SYNTHETIC:
            if self.origin is not ValueOrigin.SYNTHETIC or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
                raise ValueError("synthetic distributions are uncalibrated")
        else:
            if filled or self.origin is not ValueOrigin.NOT_IMPLEMENTED:
                raise ValueError("an unfinished distribution cannot carry numbers")
            if self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
                raise ValueError("an unfinished distribution is not calibrated")
        return self
