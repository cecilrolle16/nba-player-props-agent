"""Experimental integer residual histogram. It is not a line probability or a bet."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from contracts.common import (
    UTCDateTime,
    GameId,
    Market,
    PlayerId,
    ProjectionId,
    SourceId,
    StrictModel,
    require_sources,
)
from contracts.distribution import StatMass
from contracts.stages import CalibrationStatus

RESIDUAL_VERSION = "2026-11-01.integer-residual-histogram"
REQUIRED_LIMITATIONS = (
    "experimental_uncalibrated",
    "no_minutes_distribution",
    "mean_preservation_not_guaranteed",
    "tail_has_no_integer_outcomes",
)


class ResidualTarget(StrictModel):
    """The pregame mean the histogram is built around. Minutes stay a single number."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    model_version: str = Field(min_length=1)
    baseline_mean: float | None = Field(default=None, allow_inf_nan=False)
    expected_minutes: float | None = Field(default=None, allow_inf_nan=False)
    decision_cutoff: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]


class ResidualTrainingRow(StrictModel):
    """One earlier prediction and the stat observed after that prediction."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    model_version: str = Field(min_length=1)
    predicted_mean: float = Field(allow_inf_nan=False)
    expected_minutes: float = Field(gt=0, allow_inf_nan=False)
    decision_cutoff: UTCDateTime
    prediction_available_at: UTCDateTime
    observed_stat: int | None = Field(default=None, ge=0)
    outcome_available_at: UTCDateTime
    played: bool | None = None
    split: Literal["train", "holdout"]
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]


class ExcludedResidual(StrictModel):
    canonical_game_id: GameId
    reason: str = Field(min_length=1)


class IntegerResidualDistribution(StrictModel):
    """Nonnegative integers plus one tail bin. The tail has no individual outcomes."""

    distribution_id: ProjectionId
    model_version: Literal["2026-11-01.integer-residual-histogram"]
    experimental: Literal[True]
    calibration_status: Literal[CalibrationStatus.UNCALIBRATED]
    validation_status: Literal["unvalidated"]
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    baseline_model_version: str = Field(min_length=1)
    baseline_mean: float = Field(ge=0, allow_inf_nan=False)
    expected_minutes: float = Field(gt=0, allow_inf_nan=False)
    minutes_mode: Literal["fixed", "pooled"]
    minutes_tolerance: float = Field(ge=0, allow_inf_nan=False)
    support_max: int = Field(ge=0)
    smoothing: float = Field(ge=0, allow_inf_nan=False)
    min_training_rows: int = Field(ge=1)
    defaults_validated: Literal[False]
    outcomes: tuple[StatMass, ...]
    tail_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    tail_above: int = Field(ge=0)
    tail_outcomes_resolved: Literal[False]
    training_count: int = Field(ge=1)
    clipped_negative_count: int = Field(ge=0)
    fractional_split_count: int = Field(ge=0)
    tail_assignment_count: int = Field(ge=0)
    distribution_mean: float | None = Field(default=None, allow_inf_nan=False)
    mean_difference: float | None = Field(default=None, allow_inf_nan=False)
    mean_preservation_guaranteed: Literal[False]
    limitations: tuple[str, ...]
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def support_and_mean(self) -> "IntegerResidualDistribution":
        stats = [item.stat for item in self.outcomes]
        if stats != list(range(self.support_max + 1)):
            raise ValueError("integer support is every nonnegative integer through the cap")
        if self.tail_above != self.support_max:
            raise ValueError("the tail starts strictly above the integer cap")
        total = sum(item.probability for item in self.outcomes) + self.tail_probability
        if abs(total - 1) > 1e-9:
            raise ValueError("integer probabilities and tail probability sum to 1")
        if any(item not in self.limitations for item in REQUIRED_LIMITATIONS):
            raise ValueError("the distribution states its limitations")
        if self.tail_probability > 1e-12:
            if self.distribution_mean is not None or self.mean_difference is not None:
                raise ValueError("an unresolved tail has no numeric distribution mean")
        else:
            if self.distribution_mean is None or self.mean_difference is None:
                raise ValueError("a fully resolved histogram reports its mean and the gap")
            resolved = sum(item.stat * item.probability for item in self.outcomes)
            if abs(self.distribution_mean - resolved) > 1e-9:
                raise ValueError("distribution mean is the probability-weighted integer stat")
            if abs(self.mean_difference - (self.distribution_mean - self.baseline_mean)) > 1e-9:
                raise ValueError("mean difference is distribution mean minus baseline mean")
        return self


class ResidualFit(StrictModel):
    status: Literal["available", "unavailable"]
    reason: str | None = None
    distribution: IntegerResidualDistribution | None = None
    excluded: tuple[ExcludedResidual, ...] = ()
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False

    @model_validator(mode="after")
    def availability_matches_the_distribution(self) -> "ResidualFit":
        if self.status == "available" and (self.distribution is None or self.reason is not None):
            raise ValueError("an available fit has a distribution and no refusal reason")
        if self.status == "unavailable" and (self.distribution is not None or not self.reason):
            raise ValueError("an unavailable fit names the reason and stores no distribution")
        return self
