"""Stat distribution for one player, game, and market. No line and no side.

A baseline mean may be stored here. `outcomes` stays empty until a later
calculator builds a distribution. A mean is not that distribution, and it is
not a calibrated confidence. Win, loss, and push probabilities belong on
`MarketComparison`.
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
    SnapshotId,
    SourceId,
    StrictModel,
    require_sources,
)
from contracts.stages import CalibrationStatus, StageState, ValueOrigin
from projection.formulas import baseline_mean, shrunk_rate, unshrunk_rate

BASELINE_VERSION = "2026-11-01.baseline-minutes-rate"


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
    model_version: Literal["none", "2026-11-01.baseline-minutes-rate"]
    stage_state: StageState
    origin: ValueOrigin
    calibration_status: CalibrationStatus
    projected_at: UTCDateTime | None = None
    available_at: UTCDateTime | None = None
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
    projection_version: str | None = None
    expected_minutes: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    minutes_uncertainty: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    minutes_snapshot_id: SnapshotId | None = None
    historical_minutes: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    historical_stat_total: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    historical_rate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    selected_rate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    shrinkage_enabled: bool | None = None
    baseline_label: Literal["unshrunk", "shrunk"] | None = None
    prior_rate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prior_minutes: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    prior_snapshot_id: SnapshotId | None = None
    coefficients_validated: Literal[False] | None = None
    missingness: str | None = None

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
            if self.model_version != "none" or self._baseline_fields():
                raise ValueError("synthetic distributions are not a baseline projection")
            return self
        if self.stage_state is StageState.CALCULATED:
            self._require_baseline_mean()
            return self
        if self.stage_state is StageState.UNAVAILABLE and self.origin is ValueOrigin.OBSERVED:
            self._require_refusal()
            return self
        if filled or self.origin is not ValueOrigin.NOT_IMPLEMENTED or self.model_version != "none":
            raise ValueError("an unfinished distribution cannot carry numbers")
        if self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
            raise ValueError("an unfinished distribution is not calibrated")
        if self._baseline_fields():
            raise ValueError("an unfinished distribution has no baseline")
        return self

    def _baseline_fields(self) -> bool:
        return any(
            (
                self.projection_version is not None,
                self.expected_minutes is not None,
                self.selected_rate is not None,
                self.historical_rate is not None,
                self.shrinkage_enabled is not None,
                self.missingness is not None,
                self.baseline_label is not None,
            )
        )

    def _require_baseline_mean(self) -> None:
        if self.origin is not ValueOrigin.OBSERVED or self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
            raise ValueError("a baseline mean is observed and is not calibrated")
        if self.outcomes:
            raise ValueError("a baseline mean does not create an outcome distribution")
        if (
            self.model_version != BASELINE_VERSION
            or self.projection_version != BASELINE_VERSION
            or self.coefficients_validated is not False
        ):
            raise ValueError("baseline coefficients are an unvalidated formula version")
        if self.missingness is not None or self.mean is None:
            raise ValueError("a calculated baseline has a mean and no missingness")
        if self.minutes_snapshot_id is None or self.expected_minutes is None:
            raise ValueError("a baseline names the expected-minutes evidence")
        if self.historical_minutes is None or self.historical_stat_total is None or self.historical_rate is None:
            raise ValueError("a baseline names the historical rate")
        if self.selected_rate is None or self.shrinkage_enabled is None or self.baseline_label is None:
            raise ValueError("a baseline names the selected rate")
        historical = unshrunk_rate(self.historical_stat_total, self.historical_minutes)
        if abs(self.historical_rate - historical) > 1e-9:
            raise ValueError("historical rate is total stat divided by historical minutes")
        if self.shrinkage_enabled:
            if self.baseline_label != "shrunk" or self.prior_rate is None or self.prior_minutes is None:
                raise ValueError("shrinkage requires a prior rate and prior minutes")
            selected = shrunk_rate(
                self.historical_stat_total,
                self.historical_minutes,
                self.prior_rate,
                self.prior_minutes,
            )
        else:
            if self.baseline_label != "unshrunk" or self.prior_rate is not None or self.prior_minutes is not None:
                raise ValueError("an unshrunk baseline does not use a prior")
            selected = historical
        if abs(self.selected_rate - selected) > 1e-9:
            raise ValueError("selected rate does not match the shrinkage setting")
        if abs(self.mean - baseline_mean(self.expected_minutes, self.selected_rate)) > 1e-9:
            raise ValueError("mean is expected minutes times the selected rate")

    def _require_refusal(self) -> None:
        if self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
            raise ValueError("a refused baseline is not calibrated")
        if self.mean is not None or self.outcomes or self.selected_rate is not None or self.historical_rate is not None:
            raise ValueError("a refused baseline has no mean")
        if (
            self.model_version != BASELINE_VERSION
            or self.projection_version != BASELINE_VERSION
            or self.coefficients_validated is not False
        ):
            raise ValueError("a refused baseline still names the formula")
        if self.missingness is None or self.missingness.strip() == "":
            raise ValueError("a refused baseline names what was missing")
