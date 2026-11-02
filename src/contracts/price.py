"""A price for one probability comparison and one quote.

Raw implied probability still contains the sportsbook's vig. A proportional
no-vig number is not a true probability. Probability edge is not the
distribution mean minus the line. Nothing here sets a threshold, a stake,
or a recommendation.
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
    PriceId,
    ProjectionId,
    Side,
    SnapshotId,
    SourceId,
    StrictModel,
    require_sources,
)
from contracts.stages import CalibrationStatus

PRICE_VERSION = "2026-11-01.american-price-edge"
REQUIRED_PRICE_LIMITATIONS = (
    "experimental_uncalibrated",
    "raw_implied_contains_vig",
    "no_vig_is_not_a_true_probability",
    "mean_minus_line_is_not_probability_edge",
    "recommendations_disabled",
)


class PricedQuote(StrictModel):
    """One side of one line at one book, with the snapshot clocks kept."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    side: Side
    line: float = Field(gt=0, allow_inf_nan=False)
    book: str = Field(min_length=1)
    american_odds: str = Field(pattern=AMERICAN_ODDS.pattern)
    snapshot_id: SnapshotId
    captured_at: UTCDateTime
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]


class PriceComparison(StrictModel):
    price_id: PriceId
    model_version: Literal["2026-11-01.american-price-edge"]
    experimental: Literal[True]
    calibration_status: Literal[CalibrationStatus.UNCALIBRATED]
    validation_status: Literal["unvalidated"]
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False
    live_recommendation: Literal[False] = False
    threshold: None = None
    stake: None = None
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
    opposite_snapshot_id: SnapshotId | None = None
    decimal_price: float | None = Field(default=None, gt=1, allow_inf_nan=False)
    raw_implied_probability: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    raw_implied_contains_vig: Literal[True] | None = None
    no_vig_probability: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    no_vig_method: Literal["proportional"] | None = None
    no_vig_is_true_probability: Literal[False] = False
    p_win: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    p_loss: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    p_push: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    conditional_win_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    probability_edge: float | None = Field(default=None, allow_inf_nan=False)
    edge_benchmark: Literal["raw_implied", "no_vig"] | None = None
    edge_reason: str | None = None
    ev_per_unit: float | None = Field(default=None, allow_inf_nan=False)
    distribution_mean: float | None = Field(default=None, allow_inf_nan=False)
    mean_minus_line: float | None = Field(default=None, allow_inf_nan=False)
    pair_status: Literal["not_supplied", "matched", "rejected", "not_evaluated"]
    pair_reason: str | None = None
    status: Literal["available", "unavailable"]
    unavailable_reason: str | None = None
    limitations: tuple[str, ...]
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]

    @model_validator(mode="after")
    def price_matches_its_status(self) -> "PriceComparison":
        if any(item not in self.limitations for item in REQUIRED_PRICE_LIMITATIONS):
            raise ValueError("the price states its limitations")
        if self.mean_minus_line is not None and self.distribution_mean is None:
            raise ValueError("mean minus line requires a resolved distribution mean")
        if self.distribution_mean is not None and self.line is not None and self.mean_minus_line is not None:
            if abs(self.mean_minus_line - (self.distribution_mean - self.line)) > 1e-9:
                raise ValueError("mean minus line is the distribution mean minus the line")
        if self.status == "available":
            return self._available()
        if not self.unavailable_reason:
            raise ValueError("an unavailable price names the reason")
        if any(
            value is not None
            for value in (
                self.decimal_price,
                self.raw_implied_probability,
                self.no_vig_probability,
                self.conditional_win_probability,
                self.probability_edge,
                self.ev_per_unit,
                self.mean_minus_line,
            )
        ):
            raise ValueError("an unavailable price does not store calculated numbers")
        if self.raw_implied_contains_vig is not None or self.edge_benchmark is not None or self.edge_reason is not None:
            raise ValueError("an unavailable price does not label a benchmark")
        return self

    def _available(self) -> "PriceComparison":
        if self.unavailable_reason is not None:
            raise ValueError("an available price has no refusal reason")
        if (
            self.line is None
            or self.side is None
            or self.book is None
            or self.american_odds is None
            or self.decimal_price is None
            or self.raw_implied_probability is None
            or self.ev_per_unit is None
            or self.p_win is None
            or self.p_loss is None
            or self.p_push is None
        ):
            raise ValueError("an available price keeps the quote and the expected value")
        if self.raw_implied_contains_vig is not True:
            raise ValueError("raw implied probability contains vig")
        if abs((self.p_win + self.p_loss + self.p_push) - 1) > 1e-9:
            raise ValueError("win, loss, and push probabilities must sum to 1")
        if self.pair_status == "matched":
            if self.no_vig_probability is None or self.no_vig_method != "proportional" or self.opposite_snapshot_id is None:
                raise ValueError("a matched pair stores the proportional no-vig price and both quotes")
        elif self.no_vig_probability is not None or self.no_vig_method is not None or self.opposite_snapshot_id is not None:
            raise ValueError("no-vig probability is stored only for a matched pair")
        if self.pair_status == "rejected" and not self.pair_reason:
            raise ValueError("a rejected pair names the reason")
        if self.pair_status != "rejected" and self.pair_reason is not None:
            raise ValueError("only a rejected pair names a pair reason")
        push_only = self.p_win + self.p_loss == 0
        if push_only:
            if (
                self.ev_per_unit != 0
                or self.conditional_win_probability is not None
                or self.probability_edge is not None
                or self.edge_benchmark is not None
                or self.edge_reason != "all_push"
            ):
                raise ValueError("an all-push price has zero EV and no probability edge")
        elif (
            self.conditional_win_probability is None
            or self.probability_edge is None
            or self.edge_benchmark is None
            or self.edge_reason is not None
        ):
            raise ValueError("probability edge names its benchmark")
        if self.edge_benchmark == "no_vig" and self.no_vig_probability is None:
            raise ValueError("a no-vig edge requires the no-vig probability")
        if self.edge_benchmark == "raw_implied" and self.pair_status == "matched":
            raise ValueError("a matched pair is the no-vig benchmark")
        if self.distribution_mean is None and self.mean_minus_line is not None:
            raise ValueError("an unresolved distribution mean leaves mean minus line empty")
        if self.distribution_mean is not None and self.mean_minus_line is None:
            raise ValueError("a resolved distribution mean stores mean minus line")
        return self
