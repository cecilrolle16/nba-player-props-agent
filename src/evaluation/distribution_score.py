"""Exact scores for an integer histogram.

Log score is `-log(p(stat))` with the natural logarithm. It is an explicit
`zero_probability_at_outcome` failure when that probability is zero, and it
is unavailable when the stat falls in the unresolved tail. An aggregate does
not omit a zero-probability outcome: if every scored outcome is identified
and any of them has probability zero, the aggregate log loss is positive
infinity. A finite mean of the remaining outcomes is not reported.

CRPS is `E|X - stat| - 0.5 E|X - X'|` for the integer bins. It is unavailable
whenever the tail probability is positive, because those outcomes have no
numeric values. Neither score is replaced by an approximation.
"""

from collections.abc import Sequence
from math import inf, isfinite, isinf, log
from typing import Literal

from pydantic import Field, model_validator

from contracts.integer_residual import IntegerResidualDistribution
from contracts.common import StrictModel


class DistributionScore(StrictModel):
    log_score: float | None = Field(default=None, allow_inf_nan=False)
    log_score_status: Literal["available", "unavailable"]
    log_score_reason: str | None = None
    crps: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    crps_status: Literal["available", "unavailable"]
    crps_reason: str | None = None
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False

    @model_validator(mode="after")
    def status_matches_the_number(self) -> "DistributionScore":
        if self.log_score_status == "available" and (self.log_score is None or self.log_score_reason is not None):
            raise ValueError("an available log score has a number and no refusal")
        if self.log_score_status == "unavailable" and (self.log_score is not None or not self.log_score_reason):
            raise ValueError("an unavailable log score names the reason")
        if self.crps_status == "available" and (self.crps is None or self.crps_reason is not None):
            raise ValueError("an available CRPS has a number and no refusal")
        if self.crps_status == "unavailable" and (self.crps is not None or not self.crps_reason):
            raise ValueError("an unavailable CRPS names the reason")
        return self


def score_integer_residual(distribution: IntegerResidualDistribution, stat: int) -> DistributionScore:
    """Score one observed integer. This does not create a market probability."""

    probability = _probability(distribution, stat)
    if stat > distribution.support_max and distribution.tail_probability > 1e-12:
        log_status, log_score, log_reason = "unavailable", None, "tail_outcome_unresolved"
    elif probability is None or probability <= 0:
        log_status, log_score, log_reason = "unavailable", None, "zero_probability_at_outcome"
    else:
        log_status, log_score, log_reason = "available", -log(probability), None
    if distribution.tail_probability > 1e-12:
        crps_status, crps, crps_reason = "unavailable", None, "tail_mass_unresolved"
    else:
        crps_status, crps, crps_reason = "available", _crps(distribution, stat), None
    return DistributionScore(
        log_score=log_score,
        log_score_status=log_status,
        log_score_reason=log_reason,
        crps=crps,
        crps_status=crps_status,
        crps_reason=crps_reason,
    )


class AggregateLogScore(StrictModel):
    """One log loss for every supplied outcome. Zero probability is not dropped."""

    case_count: int = Field(ge=0)
    zero_probability_count: int = Field(ge=0)
    unresolved_tail_count: int = Field(ge=0)
    log_loss: float | None = Field(default=None, allow_inf_nan=True)
    log_loss_status: Literal["available", "positive_infinity", "unavailable"]
    log_loss_reason: str | None = None
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False

    @model_validator(mode="after")
    def status_matches_the_aggregate(self) -> "AggregateLogScore":
        if self.zero_probability_count + self.unresolved_tail_count > self.case_count:
            raise ValueError("refused outcomes are part of the scored sample")
        if self.log_loss_status == "available":
            if (
                self.log_loss is None
                or not isfinite(self.log_loss)
                or self.log_loss_reason is not None
                or self.zero_probability_count
                or self.unresolved_tail_count
                or self.case_count < 1
            ):
                raise ValueError("an available aggregate log loss is the finite mean of every case")
        elif self.log_loss_status == "positive_infinity":
            if (
                self.log_loss is None
                or not isinf(self.log_loss)
                or self.log_loss < 0
                or self.log_loss_reason != "zero_probability_at_outcome"
                or self.zero_probability_count < 1
                or self.unresolved_tail_count
            ):
                raise ValueError("positive infinity is the log loss of an identified zero probability")
        elif self.log_loss is not None or not self.log_loss_reason:
            raise ValueError("an unavailable aggregate log loss names the reason and stores no number")
        return self


def aggregate_log_loss(cases: Sequence[tuple[IntegerResidualDistribution, int]]) -> AggregateLogScore:
    """Average identified log losses. A zero probability makes the result infinite.

    An unresolved tail outcome makes the aggregate unavailable. The finite
    scores in that sample are not averaged by themselves, and a zero-probability
    outcome in that sample stays in `zero_probability_count`.
    """

    finite: list[float] = []
    zeros = 0
    tails = 0
    for distribution, stat in cases:
        kind, value = _classified_log_loss(distribution, stat)
        if kind == "tail":
            tails += 1
        elif kind == "zero":
            zeros += 1
        else:
            assert value is not None
            finite.append(value)
    count = len(cases)
    if tails:
        return AggregateLogScore(
            case_count=count,
            zero_probability_count=zeros,
            unresolved_tail_count=tails,
            log_loss=None,
            log_loss_status="unavailable",
            log_loss_reason="tail_outcome_unresolved",
        )
    if zeros:
        return AggregateLogScore(
            case_count=count,
            zero_probability_count=zeros,
            unresolved_tail_count=0,
            log_loss=inf,
            log_loss_status="positive_infinity",
            log_loss_reason="zero_probability_at_outcome",
        )
    if count == 0:
        return AggregateLogScore(
            case_count=0,
            zero_probability_count=0,
            unresolved_tail_count=0,
            log_loss=None,
            log_loss_status="unavailable",
            log_loss_reason="empty_sample",
        )
    return AggregateLogScore(
        case_count=count,
        zero_probability_count=0,
        unresolved_tail_count=0,
        log_loss=sum(finite) / count,
        log_loss_status="available",
        log_loss_reason=None,
    )


def _classified_log_loss(distribution: IntegerResidualDistribution, stat: int) -> tuple[str, float | None]:
    if stat > distribution.support_max and distribution.tail_probability > _TAIL_DUST:
        return "tail", None
    probability = _probability(distribution, stat)
    if probability is None or probability <= 0:
        return "zero", None
    return "finite", -log(probability)


_TAIL_DUST = 1e-12


def _probability(distribution: IntegerResidualDistribution, stat: int) -> float | None:
    if stat < 0 or stat > distribution.support_max:
        return None
    return distribution.outcomes[stat].probability


def _crps(distribution: IntegerResidualDistribution, stat: int) -> float:
    weights = [item.probability for item in distribution.outcomes]
    absolute = sum(probability * abs(point - stat) for point, probability in enumerate(weights))
    pairwise = 0.0
    for left, left_probability in enumerate(weights):
        for right, right_probability in enumerate(weights):
            pairwise += left_probability * right_probability * abs(left - right)
    return absolute - 0.5 * pairwise
