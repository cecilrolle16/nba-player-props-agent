"""One quote for one player, event, stat, line, side, and odds snapshot.

Win, loss, and push probabilities may be calculated from an experimental
integer distribution. Implied probability and the no-vig price stay null
on this record. `src/edge` prices a separate record and does not turn it
into a recommendation.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from contracts.common import (
    AMERICAN_ODDS,
    UTCDateTime,
    ComparisonId,
    GameId,
    Market,
    PlayerId,
    ProjectionId,
    Side,
    SnapshotId,
    SourceId,
    StrictModel,
    require_sources,
)
from contracts.integer_residual import RESIDUAL_VERSION
from contracts.stages import CalibrationStatus, StageState, ValueOrigin


class MarketComparison(StrictModel):
    comparison_id: ComparisonId
    canonical_projection_id: ProjectionId
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    line: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    side: Side | None = None
    book: str | None = None
    american_odds: str | None = Field(default=None, pattern=AMERICAN_ODDS.pattern)
    odds_snapshot_id: SnapshotId
    paired: bool | None = None
    p_win: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    p_loss: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    p_push: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    implied_probability: None = None
    no_vig_probability: None = None
    price_reference: Literal["unresolved"] = "unresolved"
    quote_captured_at: UTCDateTime | None = None
    quote_available_at: UTCDateTime | None = None
    rules_version: str = Field(min_length=1)
    model_version: Literal["none", "2026-11-01.integer-residual-histogram"]
    stage_state: StageState
    origin: ValueOrigin
    calibration_status: CalibrationStatus
    unavailable_reason: str | None = None
    experimental: bool = False
    minutes_mode: Literal["fixed", "pooled"] | None = None
    minutes_tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    smoothing: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    limitations: tuple[str, ...] = ()
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def probabilities_stay_on_the_quote(self) -> "MarketComparison":
        probabilities = (self.p_win, self.p_loss, self.p_push)
        if any(value is not None for value in probabilities) and any(value is None for value in probabilities):
            raise ValueError("win, loss, and push probabilities are set together")
        if all(value is not None for value in probabilities) and abs(sum(probabilities) - 1) > 1e-9:
            raise ValueError("win, loss, and push probabilities must sum to 1")
        if self.stage_state is StageState.SYNTHETIC:
            return self._synthetic(probabilities)
        if self.model_version == RESIDUAL_VERSION:
            return self._residual(probabilities)
        return self._unfinished(probabilities)

    def _synthetic(self, probabilities: tuple[float | None, float | None, float | None]) -> "MarketComparison":
        if self.origin is not ValueOrigin.SYNTHETIC or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
            raise ValueError("synthetic comparisons are uncalibrated")
        if self.model_version != "none" or self.experimental or self.unavailable_reason is not None:
            raise ValueError("a synthetic comparison is not an experimental residual result")
        if self.minutes_mode is not None or self.minutes_tolerance is not None or self.smoothing is not None or self.limitations:
            raise ValueError("a synthetic comparison does not carry residual fit metadata")
        if self.paired is None or self.line is None or self.side is None or self.book is None or self.american_odds is None:
            raise ValueError("a synthetic comparison names the book, line, side, and whether a pair exists")
        if self.quote_captured_at is None or self.quote_available_at is None:
            raise ValueError("a synthetic comparison keeps the odds snapshot clocks")
        if any(value is not None for value in probabilities):
            if self.line is None or self.side is None or self.book is None or self.american_odds is None:
                raise ValueError("side probabilities require the line, side, book, and American odds")
        return self

    def _residual(self, probabilities: tuple[float | None, float | None, float | None]) -> "MarketComparison":
        if self.origin is not ValueOrigin.OBSERVED or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
            raise ValueError("a residual side probability is observed and uncalibrated")
        if self.experimental is not True:
            raise ValueError("a residual side probability stays experimental")
        if self.minutes_mode is None or self.minutes_tolerance is None or self.smoothing is None:
            raise ValueError("a residual side probability keeps its minutes and smoothing assumptions")
        if "experimental_uncalibrated" not in self.limitations or "no_minutes_distribution" not in self.limitations:
            raise ValueError("a residual side probability states its limitations")
        if self.quote_captured_at is None or self.quote_available_at is None:
            raise ValueError("side probabilities keep the odds snapshot clocks")
        if self.book is not None or self.american_odds is not None or self.paired is not None:
            raise ValueError("a residual side probability does not select a price")
        if self.stage_state is StageState.CALCULATED:
            if self.unavailable_reason is not None or any(value is None for value in probabilities):
                raise ValueError("a calculated comparison stores win, loss, and push and no refusal")
            if self.line is None or self.side is None:
                raise ValueError("side probabilities stay on the line and side")
            return self
        if self.stage_state is not StageState.UNAVAILABLE or not self.unavailable_reason:
            raise ValueError("an unfinished residual comparison names why it is unavailable")
        if any(value is not None for value in probabilities):
            raise ValueError("an unavailable side probability stores no probabilities")
        if self.line is None and self.unavailable_reason != "invalid_line":
            raise ValueError("an unavailable side probability stays bound to the line")
        if self.side is None and self.unavailable_reason not in ("invalid_side", "invalid_line"):
            raise ValueError("an unavailable side probability stays bound to the side")
        return self

    def _unfinished(self, probabilities: tuple[float | None, float | None, float | None]) -> "MarketComparison":
        if self.origin is not ValueOrigin.NOT_IMPLEMENTED or self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
            raise ValueError("an unfinished comparison has no synthetic price")
        if self.model_version != "none" or self.experimental or self.unavailable_reason is not None:
            raise ValueError("an unfinished comparison is not an experimental residual result")
        if self.minutes_mode is not None or self.minutes_tolerance is not None or self.smoothing is not None or self.limitations:
            raise ValueError("an unfinished comparison does not carry residual fit metadata")
        priced = self.line is not None or self.side is not None or self.book is not None or self.american_odds is not None
        if priced or any(value is not None for value in probabilities):
            raise ValueError("an unfinished comparison does not select a price or a side probability")
        return self
