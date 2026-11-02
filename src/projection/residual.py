"""Integer residual histogram.

Transformation, applied to each accepted training row before any smoothing:

1. residual = observed_stat - predicted_mean. This is a real number.
2. location = target_baseline_mean + residual.
   The training residual mean is not subtracted. The average location equals
   the baseline mean only when that residual mean is already zero.
3. Place the row's mass of 1/n on nonnegative integers or on one tail bin.
   - location < 0: the whole mass goes to 0. No mass is placed on a negative
     integer. This moves the mean relative to the raw location.
   - location > support_max: the whole mass goes to the tail bin
     `stat > support_max`. The tail has no individual outcomes.
   - location is an integer in 0..support_max, within 1e-9: the whole mass
     goes to that integer.
   - otherwise floor(location) = k and fraction = location - k. Mass
     (1 - fraction) / n goes to k and fraction / n goes to k + 1. Both
     integers are inside the cap. This is the only split of one row.

Smoothing, when the configured pseudocount is positive, adds that count to
every integer bin and to the tail bin, then divides by the new total. That
redistributes probability, including into the tail. A positive tail makes
the numeric distribution mean unavailable, because the tail has no stat value.

The numeric mean, when the tail mass is zero, is sum(stat * probability).
Its difference from the baseline mean is reported. Exact preservation is
not guaranteed. A single expected-minutes value is a conditioning fact, not
a distribution over minutes.
"""

from math import floor

from contracts.configuration import IntegerResidualSettings
from contracts.distribution import BASELINE_VERSION, StatMass
from contracts.stages import CalibrationStatus
from contracts.integer_residual import (
    REQUIRED_LIMITATIONS,
    RESIDUAL_VERSION,
    ExcludedResidual,
    IntegerResidualDistribution,
    ResidualFit,
    ResidualTarget,
    ResidualTrainingRow,
)

DEFAULT_RESIDUAL = IntegerResidualSettings(
    version=1,
    support_max=60,
    smoothing=0,
    min_training_rows=8,
    minutes_mode="fixed",
    minutes_tolerance=0,
    defaults_validated=False,
)
_INTEGER_TOLERANCE = 1e-9


def fit_integer_residual(
    target: ResidualTarget,
    rows: tuple[ResidualTrainingRow, ...],
    settings: IntegerResidualSettings | None = None,
) -> ResidualFit:
    """Fit on accepted training rows only. Holdout rows are excluded."""

    chosen = settings or DEFAULT_RESIDUAL
    if chosen.minutes_mode not in ("fixed", "pooled"):
        return _unavailable("unsupported_minutes_conditioning", ())
    refusal = _target_refusal(target)
    if refusal is not None:
        return _unavailable(refusal, ())
    assert target.baseline_mean is not None
    assert target.expected_minutes is not None
    accepted: list[ResidualTrainingRow] = []
    excluded: list[ExcludedResidual] = []
    for row in rows:
        reason = _row_reason(row, target, chosen)
        if reason is None:
            accepted.append(row)
        else:
            excluded.append(ExcludedResidual(canonical_game_id=row.canonical_game_id, reason=reason))
    excluded_rows = tuple(sorted(excluded, key=lambda item: (item.canonical_game_id, item.reason)))
    if len(accepted) < chosen.min_training_rows:
        return _unavailable("insufficient_training", excluded_rows)
    bins, tail, counts = _histogram(accepted, target.baseline_mean, chosen)
    if chosen.smoothing > 0:
        bins = [mass + chosen.smoothing for mass in bins]
        tail += chosen.smoothing
    scale = sum(bins) + tail
    bins = [mass / scale for mass in bins]
    tail = tail / scale
    mean, difference = _means(bins, tail, target.baseline_mean)
    limitations = REQUIRED_LIMITATIONS
    if chosen.minutes_mode == "pooled":
        limitations = limitations + ("minutes_pooled_across_expected_minutes",)
    distribution = IntegerResidualDistribution(
        distribution_id=f"projection:integer-residual-{target.market.value}",
        model_version=RESIDUAL_VERSION,
        experimental=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_status="unvalidated",
        canonical_player_id=target.canonical_player_id,
        canonical_game_id=target.canonical_game_id,
        market=target.market,
        baseline_model_version=target.model_version,
        baseline_mean=target.baseline_mean,
        expected_minutes=target.expected_minutes,
        minutes_mode=chosen.minutes_mode,
        minutes_tolerance=chosen.minutes_tolerance,
        support_max=chosen.support_max,
        smoothing=chosen.smoothing,
        min_training_rows=chosen.min_training_rows,
        defaults_validated=False,
        outcomes=tuple(StatMass(stat=stat, probability=probability) for stat, probability in enumerate(bins)),
        tail_probability=tail,
        tail_above=chosen.support_max,
        tail_outcomes_resolved=False,
        training_count=len(accepted),
        clipped_negative_count=counts["clipped"],
        fractional_split_count=counts["split"],
        tail_assignment_count=counts["tail"],
        distribution_mean=mean,
        mean_difference=difference,
        mean_preservation_guaranteed=False,
        limitations=limitations,
        source_ids=target.source_ids,
    )
    return ResidualFit(status="available", distribution=distribution, excluded=excluded_rows)


def _target_refusal(target: ResidualTarget) -> str | None:
    if target.model_version != BASELINE_VERSION:
        return "model_mismatch"
    if target.baseline_mean is None or target.baseline_mean < 0:
        return "invalid_baseline_mean"
    if target.expected_minutes is None or target.expected_minutes <= 0:
        return "invalid_expected_minutes"
    return None


def _row_reason(
    row: ResidualTrainingRow,
    target: ResidualTarget,
    settings: IntegerResidualSettings,
) -> str | None:
    if row.split == "holdout":
        return "holdout"
    if row.canonical_game_id == target.canonical_game_id:
        return "target_game"
    if row.canonical_player_id != target.canonical_player_id:
        return "player_mismatch"
    if row.market is not target.market:
        return "market_mismatch"
    if row.model_version != BASELINE_VERSION:
        return "model_mismatch"
    if row.prediction_available_at >= row.decision_cutoff:
        return "prediction_after_cutoff"
    if row.decision_cutoff >= target.decision_cutoff:
        return "training_cutoff_not_before_target"
    if row.outcome_available_at >= target.decision_cutoff:
        return "outcome_after_cutoff"
    if row.outcome_available_at <= row.decision_cutoff:
        return "outcome_before_prediction_cutoff"
    if row.played is False:
        return "did_not_play"
    if row.observed_stat is None:
        return "missing_outcome"
    if settings.minutes_mode == "fixed":
        assert target.expected_minutes is not None
        if abs(row.expected_minutes - target.expected_minutes) > settings.minutes_tolerance:
            return "minutes_not_compatible"
    return None


def _histogram(
    rows: list[ResidualTrainingRow],
    baseline_mean: float,
    settings: IntegerResidualSettings,
) -> tuple[list[float], float, dict[str, int]]:
    count = len(rows)
    bins = [0.0] * (settings.support_max + 1)
    tail = 0.0
    counts = {"clipped": 0, "split": 0, "tail": 0}
    share = 1.0 / count
    for row in rows:
        assert row.observed_stat is not None
        location = baseline_mean + (row.observed_stat - row.predicted_mean)
        if location < 0:
            bins[0] += share
            counts["clipped"] += 1
            continue
        if location > settings.support_max and abs(location - settings.support_max) > _INTEGER_TOLERANCE:
            tail += share
            counts["tail"] += 1
            continue
        if abs(location - round(location)) <= _INTEGER_TOLERANCE:
            bins[int(round(location))] += share
            continue
        lower = int(floor(location))
        fraction = location - lower
        bins[lower] += (1.0 - fraction) * share
        bins[lower + 1] += fraction * share
        counts["split"] += 1
    return bins, tail, counts


def _means(bins: list[float], tail: float, baseline_mean: float) -> tuple[float | None, float | None]:
    if tail > 1e-12:
        return None, None
    mean = sum(stat * probability for stat, probability in enumerate(bins))
    return mean, mean - baseline_mean


def _unavailable(reason: str, excluded: tuple[ExcludedResidual, ...]) -> ResidualFit:
    return ResidualFit(status="unavailable", reason=reason, excluded=excluded)
