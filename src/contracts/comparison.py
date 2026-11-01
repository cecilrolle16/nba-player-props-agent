"""One price for one player, event, stat, line, side, book, and odds snapshot.

`p_win`, `p_loss`, and `p_push` are the extension fields a later calculator
would fill from a stat distribution and this line. They stay null unless a
synthetic stage supplies them. Implied probability and the no-vig price stay
null until that temporary restriction is lifted. This module does not
calculate either.
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
    model_version: Literal["none"]
    stage_state: StageState
    origin: ValueOrigin
    calibration_status: CalibrationStatus
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def probabilities_stay_on_the_price(self) -> "MarketComparison":
        probabilities = (self.p_win, self.p_loss, self.p_push)
        if any(value is not None for value in probabilities) and any(value is None for value in probabilities):
            raise ValueError("win, loss, and push probabilities are set together")
        if all(value is not None for value in probabilities) and abs(sum(probabilities) - 1) > 1e-9:
            raise ValueError("win, loss, and push probabilities must sum to 1")
        priced = self.line is not None or self.side is not None or self.book is not None or self.american_odds is not None
        if any(value is not None for value in probabilities):
            if self.line is None or self.side is None or self.book is None or self.american_odds is None:
                raise ValueError("side probabilities require the line, side, book, and American odds")
            if self.quote_captured_at is None or self.quote_available_at is None:
                raise ValueError("side probabilities keep the odds snapshot clocks")
        if self.stage_state is StageState.SYNTHETIC:
            if self.origin is not ValueOrigin.SYNTHETIC or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
                raise ValueError("synthetic comparisons are uncalibrated")
            if self.paired is None or self.line is None or self.side is None or self.book is None or self.american_odds is None:
                raise ValueError("a synthetic comparison names the book, line, side, and whether a pair exists")
            if self.quote_captured_at is None or self.quote_available_at is None:
                raise ValueError("a synthetic comparison keeps the odds snapshot clocks")
        else:
            if self.origin is not ValueOrigin.NOT_IMPLEMENTED or self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
                raise ValueError("an unfinished comparison has no synthetic price")
            if priced or any(value is not None for value in probabilities):
                raise ValueError("an unfinished comparison does not select a price or a side probability")
        return self
