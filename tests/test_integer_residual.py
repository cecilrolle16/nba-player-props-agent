"""Hand-checked integer residual histograms. These tests do not measure accuracy."""

from datetime import datetime, timezone
from math import log

from pydantic import ValidationError

from contracts.common import AnalysisState, Market
from contracts.configuration import IntegerResidualSettings, load_integer_residual
from contracts.distribution import BASELINE_VERSION
from evaluation.distribution_score import score_integer_residual
from projection.residual import DEFAULT_RESIDUAL, fit_integer_residual
from contracts.integer_residual import ResidualTarget, ResidualTrainingRow
from support import ROOT

PLAYER = "player:synthetic-guard-a"
TARGET = "game:synthetic-target"
CUTOFF = datetime(2026, 11, 2, tzinfo=timezone.utc)
TRAIN_CUTOFF = datetime(2026, 10, 1, tzinfo=timezone.utc)
PREDICTION_AT = datetime(2026, 9, 30, tzinfo=timezone.utc)
OUTCOME_AT = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _settings(**updates: object) -> IntegerResidualSettings:
    payload = {
        "version": 1,
        "support_max": 5,
        "smoothing": 0,
        "min_training_rows": 1,
        "minutes_mode": "fixed",
        "minutes_tolerance": 0,
        "defaults_validated": False,
    }
    payload.update(updates)
    return IntegerResidualSettings.model_validate(payload)


def _target(mean: float | None = 2.5, minutes: float | None = 30, *, model_version: str = BASELINE_VERSION) -> ResidualTarget:
    return ResidualTarget(
        canonical_player_id=PLAYER,
        canonical_game_id=TARGET,
        market=Market.POINTS,
        model_version=model_version,
        baseline_mean=mean,
        expected_minutes=minutes,
        decision_cutoff=CUTOFF,
        source_ids=({"provider_id": "synthetic-baseline", "native_id": "target"},),
    )


def _row(
    game_id: str,
    predicted: float,
    actual: int | None,
    *,
    minutes: float = 30,
    split: str = "train",
    player: str = PLAYER,
    market: Market = Market.POINTS,
    outcome_at: datetime = OUTCOME_AT,
    prediction_at: datetime = PREDICTION_AT,
    decision_cutoff: datetime = TRAIN_CUTOFF,
    played: bool | None = True,
    model_version: str = BASELINE_VERSION,
) -> ResidualTrainingRow:
    return ResidualTrainingRow(
        canonical_player_id=player,
        canonical_game_id=game_id,
        market=market,
        model_version=model_version,
        predicted_mean=predicted,
        expected_minutes=minutes,
        decision_cutoff=decision_cutoff,
        prediction_available_at=prediction_at,
        observed_stat=actual,
        outcome_available_at=outcome_at,
        played=played,
        split=split,
        source_ids=({"provider_id": "synthetic-logs", "native_id": game_id},),
    )


def _probability(fit, stat: int) -> float:
    assert fit.distribution is not None
    return fit.distribution.outcomes[stat].probability


def test_fractional_shift_splits_the_two_nearest_integers():
    fit = fit_integer_residual(_target(2.5), (_row("game:synthetic-a", 2, 2),), _settings())
    assert fit.status == "available"
    distribution = fit.distribution
    assert distribution is not None
    assert distribution.experimental is True
    assert distribution.calibration_status.value == "uncalibrated"
    assert distribution.recommendations_enabled is False
    assert distribution.hypothesis_adjustments_enabled is False
    assert distribution.mean_preservation_guaranteed is False
    assert distribution.fractional_split_count == 1
    assert _probability(fit, 2) == 0.5
    assert _probability(fit, 3) == 0.5
    assert distribution.tail_probability == 0
    assert sum(item.probability for item in distribution.outcomes) + distribution.tail_probability == 1
    assert distribution.distribution_mean == 2.5
    assert distribution.mean_difference == 0
    score = score_integer_residual(distribution, 2)
    assert score.log_score == log(2)
    assert score.crps == 0.25


def test_zero_mean_negative_shift_and_normalization():
    zero = fit_integer_residual(_target(0), (_row("game:synthetic-zero", 0, 0),), _settings())
    assert zero.distribution is not None
    assert _probability(zero, 0) == 1
    assert zero.distribution.distribution_mean == 0
    assert zero.distribution.mean_difference == 0

    clipped = fit_integer_residual(_target(3), (_row("game:synthetic-low", 10, 2),), _settings())
    assert clipped.distribution is not None
    assert clipped.distribution.clipped_negative_count == 1
    assert _probability(clipped, 0) == 1
    assert all(item.stat >= 0 for item in clipped.distribution.outcomes)
    assert clipped.distribution.distribution_mean == 0
    assert clipped.distribution.mean_difference == -3

    normalized = fit_integer_residual(
        _target(1),
        (_row("game:synthetic-b", 1, 2), _row("game:synthetic-a", 1, 1)),
        _settings(min_training_rows=2),
    )
    assert normalized.distribution is not None
    assert _probability(normalized, 1) == 0.5
    assert _probability(normalized, 2) == 0.5
    assert sum(item.probability for item in normalized.distribution.outcomes) == 1
    assert normalized.distribution.distribution_mean == 1.5
    assert normalized.distribution.mean_difference == 0.5
    reversed_fit = fit_integer_residual(
        _target(1),
        (_row("game:synthetic-a", 1, 1), _row("game:synthetic-b", 1, 2)),
        _settings(min_training_rows=2),
    )
    assert reversed_fit.distribution is not None
    assert [item.probability for item in reversed_fit.distribution.outcomes] == [
        item.probability for item in normalized.distribution.outcomes
    ]


def test_sparse_unseen_tail_future_and_holdout():
    sparse = fit_integer_residual(_target(1), (_row("game:synthetic-only", 1, 1),), _settings(min_training_rows=2))
    assert sparse.status == "unavailable"
    assert sparse.reason == "insufficient_training"
    assert sparse.distribution is None

    point = fit_integer_residual(_target(0), (_row("game:synthetic-zero", 0, 0),), _settings())
    assert point.distribution is not None
    unseen = score_integer_residual(point.distribution, 4)
    assert unseen.log_score_status == "unavailable"
    assert unseen.log_score_reason == "zero_probability_at_outcome"
    assert unseen.log_score is None
    assert unseen.crps_status == "available"

    tailed = fit_integer_residual(
        _target(1),
        (_row("game:synthetic-inside", 1, 1), _row("game:synthetic-above", 0, 5)),
        _settings(support_max=2, min_training_rows=2),
    )
    assert tailed.distribution is not None
    assert tailed.distribution.outcomes[1].probability == 0.5
    assert tailed.distribution.tail_probability == 0.5
    assert tailed.distribution.tail_outcomes_resolved is False
    assert tailed.distribution.distribution_mean is None
    assert tailed.distribution.mean_difference is None
    above = score_integer_residual(tailed.distribution, 5)
    assert above.log_score_reason == "tail_outcome_unresolved"
    assert above.crps_reason == "tail_mass_unresolved"
    inside = score_integer_residual(tailed.distribution, 1)
    assert inside.log_score == log(2)
    assert inside.crps_status == "unavailable"

    future = fit_integer_residual(
        _target(2),
        (
            _row("game:synthetic-kept", 2, 2),
            _row("game:synthetic-future", 2, 9, outcome_at=CUTOFF),
        ),
        _settings(),
    )
    assert future.distribution is not None
    assert future.distribution.training_count == 1
    assert _probability(future, 2) == 1
    assert future.excluded[0].reason == "outcome_after_cutoff"

    holdout = fit_integer_residual(
        _target(1),
        (
            _row("game:synthetic-train", 1, 1),
            _row("game:synthetic-holdout", 1, 4, split="holdout"),
        ),
        _settings(),
    )
    assert holdout.distribution is not None
    assert holdout.distribution.training_count == 1
    assert _probability(holdout, 1) == 1
    assert holdout.excluded[0].reason == "holdout"


def test_minutes_must_be_conditioned_or_explicitly_pooled():
    settings = _settings()
    refused = fit_integer_residual(_target(1, 30), (_row("game:synthetic-other-minutes", 1, 1, minutes=20),), settings)
    assert refused.status == "unavailable"
    assert refused.excluded[0].reason == "minutes_not_compatible"
    pooled = fit_integer_residual(
        _target(1, 30),
        (_row("game:synthetic-other-minutes", 1, 1, minutes=20),),
        _settings(minutes_mode="pooled"),
    )
    assert pooled.distribution is not None
    assert pooled.distribution.minutes_mode == "pooled"
    assert "no_minutes_distribution" in pooled.distribution.limitations
    assert "minutes_pooled_across_expected_minutes" in pooled.distribution.limitations
    mismatched = fit_integer_residual(
        _target(1),
        (_row("game:synthetic-other-player", 1, 1, player="player:synthetic-other"),),
        _settings(),
    )
    assert mismatched.status == "unavailable"
    assert mismatched.excluded[0].reason == "player_mismatch"


def test_smoothing_moves_mass_into_the_tail_and_the_mean_becomes_unavailable():
    fit = fit_integer_residual(
        _target(0),
        (_row("game:synthetic-zero", 0, 0),),
        _settings(support_max=1, smoothing=1),
    )
    assert fit.distribution is not None
    assert fit.distribution.outcomes[0].probability == 0.5
    assert fit.distribution.outcomes[1].probability == 0.25
    assert fit.distribution.tail_probability == 0.25
    assert fit.distribution.distribution_mean is None


def test_shipped_residual_settings_are_unvalidated():
    status, parsed = load_integer_residual(ROOT / "config" / "integer-residual.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert parsed == DEFAULT_RESIDUAL
    assert parsed.defaults_validated is False
    assert parsed.minutes_mode == "fixed"
    try:
        IntegerResidualSettings.model_validate(
            {
                "version": 1,
                "support_max": 60,
                "smoothing": 0,
                "min_training_rows": 8,
                "minutes_mode": "fixed",
                "minutes_tolerance": 0,
                "defaults_validated": True,
            }
        )
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True
