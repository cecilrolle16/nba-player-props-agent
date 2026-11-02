"""Hand-checked baseline scores. These numbers are not a claim of accuracy."""

from datetime import datetime, timezone

from contracts.common import Market
from contracts.distribution import BASELINE_VERSION
from evaluation.score import (
    BaselinePrediction,
    EvaluationPair,
    ObservedStat,
    evaluate_baseline,
)

PLAYER = "player:synthetic-guard-a"
OTHER = "player:synthetic-other"
CUTOFF = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
PREDICTION_AT = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
OUTCOME_AT = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)


def _prediction(
    market: Market,
    game_id: str,
    mean: float | None,
    *,
    player: str = PLAYER,
    available_at: datetime = PREDICTION_AT,
    cutoff: datetime = CUTOFF,
    model_version: str = BASELINE_VERSION,
    missingness: str | None = None,
) -> BaselinePrediction:
    return BaselinePrediction(
        canonical_player_id=player,
        canonical_game_id=game_id,
        market=market,
        mean=mean,
        model_version=model_version,
        decision_cutoff=cutoff,
        available_at=available_at,
        source_ids=({"provider_id": "synthetic-baseline", "native_id": game_id},),
        missingness=missingness,
    )


def _outcome(
    market: Market,
    game_id: str,
    stat: int | None,
    *,
    player: str = PLAYER,
    available_at: datetime = OUTCOME_AT,
    played: bool | None = True,
) -> ObservedStat:
    return ObservedStat(
        canonical_player_id=player,
        canonical_game_id=game_id,
        market=market,
        stat=stat,
        played=played,
        available_at=available_at,
        source_ids=({"provider_id": "synthetic-logs", "native_id": game_id},),
    )


def _pair(prediction: BaselinePrediction, outcome: ObservedStat | None = None) -> EvaluationPair:
    return EvaluationPair(prediction=prediction, outcome=outcome)


def _scored_sample() -> tuple[EvaluationPair, ...]:
    return (
        _pair(_prediction(Market.POINTS, "game:synthetic-b", 14), _outcome(Market.POINTS, "game:synthetic-b", 12)),
        _pair(_prediction(Market.POINTS, "game:synthetic-a", 10), _outcome(Market.POINTS, "game:synthetic-a", 8)),
        _pair(_prediction(Market.ASSISTS, "game:synthetic-c", 5), _outcome(Market.ASSISTS, "game:synthetic-c", 8)),
        _pair(_prediction(Market.REBOUNDS, "game:synthetic-d", 7), _outcome(Market.REBOUNDS, "game:synthetic-d", 7)),
        _pair(
            _prediction(Market.POINTS, "game:synthetic-mismatch", 100),
            _outcome(Market.POINTS, "game:synthetic-mismatch", 0, player=OTHER),
        ),
        _pair(
            _prediction(
                Market.POINTS,
                "game:synthetic-late-prediction",
                100,
                available_at=datetime(2026, 11, 2, 0, 0, 1, tzinfo=timezone.utc),
            ),
            _outcome(Market.POINTS, "game:synthetic-late-prediction", 0),
        ),
        _pair(
            _prediction(Market.POINTS, "game:synthetic-early-outcome", 100),
            _outcome(Market.POINTS, "game:synthetic-early-outcome", 0, available_at=CUTOFF),
        ),
        _pair(
            _prediction(Market.POINTS, "game:synthetic-missing-mean", None, missingness="insufficient_history"),
            _outcome(Market.POINTS, "game:synthetic-missing-mean", 50),
        ),
        _pair(
            _prediction(Market.ASSISTS, "game:synthetic-other-model", 4, model_version="none"),
            _outcome(Market.ASSISTS, "game:synthetic-other-model", 4),
        ),
    )


def test_errors_match_the_hand_calculation_and_ignore_input_order():
    forward = evaluate_baseline(_scored_sample())
    backward = evaluate_baseline(tuple(reversed(_scored_sample())))
    assert forward.report_id == backward.report_id
    assert forward.model_dump() == backward.model_dump()
    assert forward.validation_status == "unvalidated"
    assert forward.calibration_status.value == "not_applicable"
    assert forward.recommendations_enabled is False
    assert forward.hypothesis_adjustments_enabled is False
    assert forward.model_version == BASELINE_VERSION

    points, assists, rebounds = forward.markets
    assert (points.sample_count, points.mae, points.rmse, points.bias) == (2, 2.0, 2.0, 2.0)
    assert (assists.sample_count, assists.mae, assists.rmse, assists.bias) == (1, 3.0, 3.0, -3.0)
    assert (rebounds.sample_count, rebounds.mae, rebounds.rmse, rebounds.bias) == (1, 0.0, 0.0, 0.0)
    assert points.excluded_count == 3
    assert points.unavailable_count == 1
    assert assists.excluded_count == 1

    scored = next(case for case in forward.cases if case.canonical_game_id == "game:synthetic-a")
    assert scored.prediction == 10
    assert scored.actual == 8
    leaked = next(case for case in forward.cases if case.canonical_game_id == "game:synthetic-early-outcome")
    assert leaked.status == "excluded"
    assert leaked.reason == "outcome_before_cutoff"
    assert leaked.actual is None
    assert leaked.prediction is None
    mismatched = next(case for case in forward.cases if case.canonical_game_id == "game:synthetic-mismatch")
    assert mismatched.reason == "identity_mismatch"
    late = next(case for case in forward.cases if case.canonical_game_id == "game:synthetic-late-prediction")
    assert late.reason == "prediction_after_cutoff"
    missing = next(case for case in forward.cases if case.canonical_game_id == "game:synthetic-missing-mean")
    assert missing.status == "unavailable"
    assert missing.reason == "insufficient_history"
    assert missing.actual is None
    other_model = next(case for case in forward.cases if case.canonical_game_id == "game:synthetic-other-model")
    assert other_model.reason == "model_mismatch"


def test_a_missing_projection_is_not_manufactured():
    report = evaluate_baseline(())
    assert [item.sample_count for item in report.markets] == [0, 0, 0]
    assert all(item.mae is None and item.rmse is None and item.bias is None for item in report.markets)
    only_outcome_missing = evaluate_baseline((_pair(_prediction(Market.POINTS, "game:synthetic-no-log", 12)),))
    assert only_outcome_missing.markets[0].sample_count == 0
    assert only_outcome_missing.markets[0].mae is None
    assert only_outcome_missing.cases[0].reason == "missing_outcome"
    assert only_outcome_missing.cases[0].prediction is None
