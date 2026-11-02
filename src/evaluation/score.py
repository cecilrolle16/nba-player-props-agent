"""Score supplied baseline means against later observed stats.

The scorer does not build a projection, invent expected minutes, or choose a
distribution. Outcomes are read only while computing the error of an eligible
pair.
"""

from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from contracts.common import UTCDateTime, GameId, Market, PlayerId, ReportId, SourceId, StrictModel, require_sources
from contracts.distribution import BASELINE_VERSION
from contracts.stages import CalibrationStatus
from evaluation.formulas import mean_absolute_error, root_mean_squared_error, signed_bias

MARKET_ORDER = (Market.POINTS, Market.ASSISTS, Market.REBOUNDS)


class BaselinePrediction(StrictModel):
    """A pregame mean. `available_at` must be strictly before `decision_cutoff`."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    mean: float | None = Field(default=None, allow_inf_nan=False)
    model_version: str = Field(min_length=1)
    decision_cutoff: UTCDateTime
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
    missingness: str | None = None


class ObservedStat(StrictModel):
    """A later counting stat. It is not an input to the projection."""

    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    stat: int | None = Field(default=None, ge=0)
    played: bool | None = None
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]


class EvaluationPair(StrictModel):
    prediction: BaselinePrediction
    outcome: ObservedStat | None = None


class EvaluationCase(StrictModel):
    market: Market
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    status: Literal["eligible", "excluded", "unavailable"]
    reason: str
    prediction: float | None = None
    actual: int | None = None

    @model_validator(mode="after")
    def actual_only_when_scored(self) -> "EvaluationCase":
        if self.status != "eligible" and self.actual is not None:
            raise ValueError("an unscored case does not keep the observed stat")
        if self.status == "eligible" and (self.prediction is None or self.actual is None):
            raise ValueError("an eligible case has a prediction and an actual")
        return self


class MarketScore(StrictModel):
    market: Market
    sample_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    mae: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rmse: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bias: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def empty_sample_has_null_scores(self) -> "MarketScore":
        if self.sample_count == 0 and (self.mae is not None or self.rmse is not None or self.bias is not None):
            raise ValueError("an empty sample does not store zero error")
        if self.sample_count != self.eligible_count:
            raise ValueError("sample count is the eligible count")
        return self


class BaselineEvaluationReport(StrictModel):
    report_id: ReportId
    model_version: Literal["2026-11-01.baseline-minutes-rate"]
    validation_status: Literal["unvalidated"]
    calibration_status: Literal[CalibrationStatus.NOT_APPLICABLE]
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False
    markets: tuple[MarketScore, MarketScore, MarketScore]
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def markets_stay_separate(self) -> "BaselineEvaluationReport":
        if tuple(item.market for item in self.markets) != MARKET_ORDER:
            raise ValueError("points, assists, and rebounds are scored separately")
        return self


def evaluate_baseline(pairs: tuple[EvaluationPair, ...]) -> BaselineEvaluationReport:
    """Score each supplied pair. Order of `pairs` does not change the report."""

    cases = tuple(sorted((_judge(pair) for pair in pairs), key=_case_key))
    markets = tuple(_market_score(market, cases) for market in MARKET_ORDER)
    body = {
        "model_version": BASELINE_VERSION,
        "validation_status": "unvalidated",
        "calibration_status": CalibrationStatus.NOT_APPLICABLE.value,
        "recommendations_enabled": False,
        "hypothesis_adjustments_enabled": False,
        "markets": [item.model_dump(mode="json") for item in markets],
        "cases": [item.model_dump(mode="json") for item in cases],
    }
    digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return BaselineEvaluationReport(
        report_id=f"report:{digest[:32]}",
        model_version=BASELINE_VERSION,
        validation_status="unvalidated",
        calibration_status=CalibrationStatus.NOT_APPLICABLE,
        markets=markets,
        cases=cases,
    )


def _judge(pair: EvaluationPair) -> EvaluationCase:
    prediction = pair.prediction
    outcome = pair.outcome
    status, reason = _status(prediction, outcome)
    actual = outcome.stat if status == "eligible" and outcome is not None else None
    return EvaluationCase(
        market=prediction.market,
        canonical_player_id=prediction.canonical_player_id,
        canonical_game_id=prediction.canonical_game_id,
        status=status,
        reason=reason,
        prediction=prediction.mean if status == "eligible" else None,
        actual=actual,
    )


def _status(prediction: BaselinePrediction, outcome: ObservedStat | None) -> tuple[str, str]:
    if prediction.model_version != BASELINE_VERSION:
        return "excluded", "model_mismatch"
    if prediction.available_at >= prediction.decision_cutoff:
        return "excluded", "prediction_after_cutoff"
    if outcome is not None and (
        outcome.canonical_player_id != prediction.canonical_player_id
        or outcome.canonical_game_id != prediction.canonical_game_id
        or outcome.market != prediction.market
    ):
        return "excluded", "identity_mismatch"
    if prediction.mean is None:
        return "unavailable", prediction.missingness or "unavailable_projection"
    if outcome is None or outcome.stat is None:
        return "unavailable", "missing_outcome"
    if outcome.available_at <= prediction.decision_cutoff:
        return "excluded", "outcome_before_cutoff"
    if outcome.played is False:
        return "excluded", "did_not_play"
    return "eligible", "scored"


def _market_score(market: Market, cases: tuple[EvaluationCase, ...]) -> MarketScore:
    selected = [case for case in cases if case.market is market]
    eligible = [case for case in selected if case.status == "eligible"]
    errors = tuple(
        float(case.prediction) - float(case.actual)
        for case in eligible
        if case.prediction is not None and case.actual is not None
    )
    return MarketScore(
        market=market,
        sample_count=len(eligible),
        eligible_count=len(eligible),
        excluded_count=sum(case.status == "excluded" for case in selected),
        unavailable_count=sum(case.status == "unavailable" for case in selected),
        mae=mean_absolute_error(errors),
        rmse=root_mean_squared_error(errors),
        bias=signed_bias(errors),
    )


def _case_key(case: EvaluationCase) -> tuple[int, str, str]:
    return (MARKET_ORDER.index(case.market), case.canonical_game_id, case.canonical_player_id)
