"""Hand-checked over/under probabilities. These tests do not measure accuracy."""

from datetime import datetime, timezone
from math import inf, log

from contracts.common import Market, SourceId
from contracts.integer_residual import REQUIRED_LIMITATIONS, IntegerResidualDistribution
from contracts.pipeline import PipelineInput
from contracts.player_log import PlayerGameLog
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from contracts.stages import CalibrationStatus, StageState
from evaluation.distribution_score import aggregate_log_loss, score_integer_residual
from pipeline.run import run_pipeline
from projection.side_probability import side_probabilities
from support import catalog, load_json, load_subject

PLAYER = "player:synthetic-guard-a"
GAME = "game:synthetic-target"
CUTOFF = datetime(2026, 11, 2, tzinfo=timezone.utc)
KNOWN_AT = datetime(2026, 11, 1, tzinfo=timezone.utc)
RULES = "2026-10-29.nba-props-angles"


def _distribution(
    probabilities: tuple[float, ...],
    *,
    tail: float = 0.0,
    player: str = PLAYER,
    game: str = GAME,
    market: Market = Market.POINTS,
    smoothing: float = 0.0,
    minutes_mode: str = "fixed",
    minutes_tolerance: float = 0.0,
) -> IntegerResidualDistribution:
    support_max = len(probabilities) - 1
    mean = None if tail > 1e-12 else sum(stat * probability for stat, probability in enumerate(probabilities))
    limitations = REQUIRED_LIMITATIONS
    if minutes_mode == "pooled":
        limitations = limitations + ("minutes_pooled_across_expected_minutes",)
    return IntegerResidualDistribution(
        distribution_id=f"projection:integer-residual-{market.value}",
        model_version="2026-11-01.integer-residual-histogram",
        experimental=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_status="unvalidated",
        canonical_player_id=player,
        canonical_game_id=game,
        market=market,
        baseline_model_version="2026-11-01.baseline-minutes-rate",
        baseline_mean=0.0 if mean is None else mean,
        expected_minutes=30,
        minutes_mode=minutes_mode,
        minutes_tolerance=minutes_tolerance,
        support_max=support_max,
        smoothing=smoothing,
        min_training_rows=1,
        defaults_validated=False,
        outcomes=tuple(
            {"stat": stat, "probability": probability} for stat, probability in enumerate(probabilities)
        ),
        tail_probability=tail,
        tail_above=support_max,
        tail_outcomes_resolved=False,
        training_count=4,
        clipped_negative_count=0,
        fractional_split_count=0,
        tail_assignment_count=0,
        distribution_mean=mean,
        mean_difference=0.0 if mean is not None else None,
        mean_preservation_guaranteed=False,
        limitations=limitations,
        source_ids=({"provider_id": "synthetic-baseline", "native_id": "histogram"},),
    )


def _quote(distribution: IntegerResidualDistribution, line: float | None, side: str | None, **updates: object):
    payload = {
        "canonical_player_id": PLAYER,
        "canonical_game_id": GAME,
        "market": Market.POINTS,
        "line": line,
        "side": side,
        "odds_snapshot_id": "snapshot:synthetic-points-1",
        "quote_captured_at": KNOWN_AT,
        "quote_available_at": KNOWN_AT,
        "distribution_available_at": KNOWN_AT,
        "decision_cutoff": CUTOFF,
        "rules_version": RULES,
        "source_ids": (SourceId(provider_id="synthetic-markets", native_id="quote-points-1"),),
    }
    payload.update(updates)
    return side_probabilities(distribution, **payload)


def test_integer_and_half_integer_lines_for_both_sides():
    # Mass 1/4 on 0, 1, 2, and 3. No tail.
    distribution = _distribution((0.25, 0.25, 0.25, 0.25))
    over_integer = _quote(distribution, 2, "over")
    under_integer = _quote(distribution, 2, "under")
    assert (over_integer.p_win, over_integer.p_loss, over_integer.p_push) == (0.25, 0.5, 0.25)
    assert (under_integer.p_win, under_integer.p_loss, under_integer.p_push) == (0.5, 0.25, 0.25)
    assert over_integer.p_win + over_integer.p_loss + over_integer.p_push == 1
    assert over_integer.line == 2
    assert over_integer.experimental is True
    assert over_integer.calibration_status is CalibrationStatus.UNCALIBRATED
    assert over_integer.model_version == "2026-11-01.integer-residual-histogram"
    assert over_integer.implied_probability is None
    assert over_integer.no_vig_probability is None
    assert over_integer.american_odds is None
    assert over_integer.book is None
    assert "exact_minutes_sample" in over_integer.limitations
    assert over_integer.minutes_mode == "fixed"

    over_half = _quote(distribution, 2.5, "over")
    under_half = _quote(distribution, 2.5, "under")
    assert (over_half.p_win, over_half.p_loss, over_half.p_push) == (0.25, 0.75, 0.0)
    assert (under_half.p_win, under_half.p_loss, under_half.p_push) == (0.75, 0.25, 0.0)
    assert over_half.p_push == 0
    assert over_half.line == 2.5


def test_tail_above_the_line_is_one_class_and_a_split_tail_is_unavailable():
    distribution = _distribution((0.25, 0.25, 0.25), tail=0.25)
    above = _quote(distribution, 2.5, "over")
    assert (above.p_win, above.p_loss, above.p_push) == (0.25, 0.75, 0.0)
    assert above.unavailable_reason is None
    on_cap = _quote(distribution, 2, "under")
    assert (on_cap.p_win, on_cap.p_loss, on_cap.p_push) == (0.5, 0.25, 0.25)

    inside = _quote(distribution, 4, "over")
    first_tail_integer = _quote(distribution, 3, "over")
    assert inside.stage_state is StageState.UNAVAILABLE
    assert inside.unavailable_reason == "tail_split_by_line"
    assert inside.p_win is None and inside.p_loss is None and inside.p_push is None
    assert inside.line == 4
    assert first_tail_integer.unavailable_reason == "tail_split_by_line"
    assert first_tail_integer.calibration_status is CalibrationStatus.UNCALIBRATED

    resolved_high_line = _quote(_distribution((1.0, 0.0, 0.0)), 10.5, "over")
    assert (resolved_high_line.p_win, resolved_high_line.p_loss, resolved_high_line.p_push) == (0.0, 1.0, 0.0)


def test_identity_mismatch_and_future_evidence_stay_unavailable():
    distribution = _distribution((1.0,))
    other_player = _quote(_distribution((1.0,), player="player:synthetic-other"), 1.5, "over")
    other_game = _quote(_distribution((1.0,), game="game:synthetic-other"), 1.5, "over")
    other_market = _quote(_distribution((1.0,), market=Market.ASSISTS), 1.5, "over")
    quote_player = _quote(distribution, 1.5, "over", quote_player_id="player:synthetic-other")
    assert other_player.unavailable_reason == "player_mismatch"
    assert other_game.unavailable_reason == "game_mismatch"
    assert other_market.unavailable_reason == "market_mismatch"
    assert quote_player.unavailable_reason == "player_mismatch"
    assert other_player.p_win is None

    future = _quote(
        distribution,
        1.5,
        "over",
        quote_available_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )
    assert future.unavailable_reason == "future_evidence"
    assert future.p_win is None
    at_cutoff = _quote(distribution, 1.5, "under", distribution_available_at=CUTOFF)
    assert (at_cutoff.p_win, at_cutoff.p_loss, at_cutoff.p_push) == (1.0, 0.0, 0.0)


def test_zero_probability_is_infinite_log_loss_and_is_not_dropped():
    distribution = _distribution((1.0, 0.0, 0.0))
    unseen = score_integer_residual(distribution, 4)
    assert unseen.log_score_status == "unavailable"
    assert unseen.log_score_reason == "zero_probability_at_outcome"
    assert unseen.log_score is None

    kept = aggregate_log_loss(((distribution, 0), (distribution, 4)))
    assert kept.case_count == 2
    assert kept.zero_probability_count == 1
    assert kept.log_loss_status == "positive_infinity"
    assert kept.log_loss == inf
    assert kept.log_loss_reason == "zero_probability_at_outcome"
    assert kept.recommendations_enabled is False

    quarters = _distribution((0.25, 0.25, 0.25, 0.25))
    finite = aggregate_log_loss(((quarters, 2), (quarters, 3)))
    assert finite.log_loss_status == "available"
    assert finite.log_loss == log(4)
    mixed = aggregate_log_loss(((quarters, 2), (quarters, 3), (quarters, 4)))
    assert mixed.case_count == 3
    assert mixed.zero_probability_count == 1
    assert mixed.log_loss == inf
    assert mixed.log_loss != finite.log_loss


def test_positive_smoothing_adds_tail_mass_that_can_block_a_line():
    smoothed = _distribution((0.5, 0.25), tail=0.25, smoothing=1)
    classified = _quote(smoothed, 0.5, "over")
    assert "positive_smoothing_adds_unresolved_tail_mass" in classified.limitations
    assert (classified.p_win, classified.p_loss, classified.p_push) == (0.5, 0.5, 0.0)
    blocked = _quote(smoothed, 5, "over")
    assert blocked.unavailable_reason == "tail_split_by_line"
    tail_score = score_integer_residual(smoothed, 5)
    assert tail_score.log_score_reason == "tail_outcome_unresolved"
    assert tail_score.crps_reason == "tail_mass_unresolved"
    aggregate = aggregate_log_loss(((smoothed, 0), (smoothed, 5)))
    assert aggregate.case_count == 2
    assert aggregate.unresolved_tail_count == 1
    assert aggregate.log_loss_status == "unavailable"
    assert aggregate.log_loss is None
    assert aggregate.log_loss != -log(0.5)


def test_pipeline_classifies_the_supplied_histogram_and_does_not_recommend():
    distribution = _distribution(
        tuple(0.75 if stat == 30 else 0.25 if stat == 20 else 0.0 for stat in range(31)),
        player="player:synthetic-guard-a",
        game="game:synthetic-2026-11-01-home-away",
    )
    assert distribution.distribution_mean == 27.5
    payload = load_json("synthetic_framework.json")
    bundle = PipelineInput(
        subject=load_subject("valid_points.json"),
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
        log_snapshot_ids=(payload["log_snapshot_id"],),
        residual_distribution=distribution,
        distribution_available_at=datetime(2026, 11, 1, 22, 50, tzinfo=timezone.utc),
    )
    report = run_pipeline(bundle, catalog())
    comparison = report.comparison
    assert comparison.stage_state is StageState.CALCULATED
    assert comparison.canonical_player_id == "player:synthetic-guard-a"
    assert comparison.canonical_game_id == "game:synthetic-2026-11-01-home-away"
    assert comparison.market is Market.POINTS
    assert comparison.line == 24.5
    assert comparison.side.value == "over"
    assert comparison.odds_snapshot_id == "snapshot:synthetic-points-1"
    assert (comparison.p_win, comparison.p_loss, comparison.p_push) == (0.75, 0.25, 0.0)
    assert comparison.implied_probability is None
    assert comparison.no_vig_probability is None
    assert comparison.american_odds is None
    assert comparison.experimental is True
    assert comparison.calibration_status is CalibrationStatus.UNCALIBRATED
    assert report.decision.result == "pass"
    assert report.decision.reason == "not_implemented"
    assert report.decision.edge is None
    assert report.decision.ev_per_unit is None
    assert report.decision.live_recommendation is False
    assert report.decision.actionable is False
    assert report.recommendation == "none"
    assert report.live_recommendation is False

    late = bundle.model_copy(
        update={"distribution_available_at": datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)}
    )
    refused = run_pipeline(late, catalog())
    assert refused.comparison.unavailable_reason == "future_evidence"
    assert refused.comparison.p_win is None
    assert refused.decision.result == "pass"
    assert refused.recommendation == "none"
