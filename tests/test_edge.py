"""Hand-checked prices. These tests do not measure profitability."""

from datetime import datetime, timedelta, timezone
from fractions import Fraction

from contracts.common import Market
from contracts.comparison import MarketComparison
from contracts.market import MarketQuoteInput, MarketSnapshot
from contracts.pipeline import PipelineInput
from contracts.player_log import PlayerGameLog
from contracts.price import PricedQuote
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from edge.formulas import american_price, conditional_win_probability
from edge.price import price_quote, priced_quote_from_input
from pipeline.run import run_pipeline
from support import catalog, load_json, load_subject
from test_side_probability import _distribution

PLAYER = "player:synthetic-guard-a"
GAME = "game:synthetic-target"
KNOWN = datetime(2026, 11, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 11, 2, tzinfo=timezone.utc)
RULES = "2026-10-29.nba-props-angles"


def _comparison(win: float, loss: float, push: float, **updates: object) -> MarketComparison:
    payload = {
        "comparison_id": "comparison:synthetic-points",
        "canonical_projection_id": "projection:synthetic-distribution",
        "canonical_player_id": PLAYER,
        "canonical_game_id": GAME,
        "market": "points",
        "line": 24.5,
        "side": "over",
        "book": "synthetic-book",
        "american_odds": "-110",
        "odds_snapshot_id": "snapshot:synthetic-points-1",
        "paired": False,
        "p_win": win,
        "p_loss": loss,
        "p_push": push,
        "quote_captured_at": KNOWN,
        "quote_available_at": KNOWN,
        "rules_version": RULES,
        "model_version": "none",
        "stage_state": "synthetic",
        "origin": "synthetic",
        "calibration_status": "uncalibrated",
        "source_ids": [{"provider_id": "synthetic-markets", "native_id": "quote-points-1"}],
    }
    payload.update(updates)
    return MarketComparison.model_validate(payload)


def _quote(**updates: object) -> PricedQuote:
    payload = {
        "canonical_player_id": PLAYER,
        "canonical_game_id": GAME,
        "market": "points",
        "side": "over",
        "line": 24.5,
        "book": "synthetic-book",
        "american_odds": "-110",
        "snapshot_id": "snapshot:synthetic-points-1",
        "captured_at": KNOWN,
        "available_at": KNOWN,
        "source_ids": ({"provider_id": "synthetic-markets", "native_id": "quote-points-1"},),
    }
    payload.update(updates)
    return PricedQuote.model_validate(payload)


def _opposite(**updates: object) -> PricedQuote:
    payload = {
        "canonical_player_id": PLAYER,
        "canonical_game_id": GAME,
        "market": "points",
        "side": "under",
        "line": 24.5,
        "book": "synthetic-book",
        "american_odds": "-110",
        "snapshot_id": "snapshot:synthetic-points-under",
        "captured_at": KNOWN,
        "available_at": KNOWN,
        "source_ids": ({"provider_id": "synthetic-markets", "native_id": "quote-points-under"},),
    }
    payload.update(updates)
    return PricedQuote.model_validate(payload)


def _price(win: float, loss: float, push: float, quote: PricedQuote | None = None, **kwargs: object):
    return price_quote(
        _comparison(win, loss, push),
        quote or _quote(),
        decision_cutoff=CUTOFF,
        **kwargs,
    )


def test_negative_and_positive_odds_match_the_hand_values():
    priced = _price(0.55, 0.40, 0.05)
    decimal, implied = american_price("-110")
    assert decimal == Fraction(21, 11)
    assert implied == Fraction(11, 21)
    assert priced.decimal_price == float(decimal)
    assert priced.raw_implied_probability == float(implied)
    assert priced.raw_implied_contains_vig is True
    assert priced.ev_per_unit == 0.1
    assert priced.no_vig_probability is None
    assert priced.edge_benchmark == "raw_implied"
    assert priced.pair_status == "not_supplied"
    conditional = conditional_win_probability(0.55, 0.40)
    assert conditional == Fraction(11, 19)
    assert priced.conditional_win_probability == float(conditional)
    assert priced.probability_edge == float(conditional - implied)
    assert priced.experimental is True
    assert priced.calibration_status.value == "uncalibrated"
    assert priced.recommendations_enabled is False
    assert priced.hypothesis_adjustments_enabled is False
    assert priced.threshold is None
    assert priced.stake is None
    assert priced.live_recommendation is False
    assert priced.no_vig_is_true_probability is False
    assert priced.mean_minus_line is None

    plus = price_quote(
        _comparison(0.5, 0.5, 0.0, american_odds="+150"),
        _quote(american_odds="+150"),
        decision_cutoff=CUTOFF,
    )
    assert plus.decimal_price == 2.5
    assert plus.raw_implied_probability == 0.4
    assert plus.ev_per_unit == 0.25
    assert plus.probability_edge == 0.1


def test_integer_push_adds_nothing_and_an_all_push_has_no_edge():
    pushed = price_quote(
        _comparison(0.50, 0.30, 0.20, line=24, american_odds="-110"),
        _quote(line=24),
        decision_cutoff=CUTOFF,
    )
    assert pushed.line == 24
    assert pushed.p_push == 0.20
    assert pushed.ev_per_unit == float(Fraction(17, 110))
    assert pushed.probability_edge == float(Fraction(1, 2) / Fraction(4, 5) - Fraction(11, 21))

    all_push = _price(0.0, 0.0, 1.0)
    assert all_push.status == "available"
    assert all_push.ev_per_unit == 0.0
    assert all_push.conditional_win_probability is None
    assert all_push.probability_edge is None
    assert all_push.edge_benchmark is None
    assert all_push.edge_reason == "all_push"
    assert all_push.decimal_price == float(Fraction(21, 11))


def test_a_matched_pair_uses_proportional_no_vig_and_a_mismatch_does_not():
    matched = _price(0.55, 0.40, 0.05, opposite=_opposite())
    assert matched.pair_status == "matched"
    assert matched.no_vig_method == "proportional"
    assert matched.no_vig_probability == 0.5
    assert matched.no_vig_is_true_probability is False
    assert matched.edge_benchmark == "no_vig"
    assert matched.odds_snapshot_id == "snapshot:synthetic-points-1"
    assert matched.opposite_snapshot_id == "snapshot:synthetic-points-under"
    assert matched.ev_per_unit == 0.1
    assert matched.probability_edge == float(Fraction(11, 19) - Fraction(1, 2))
    assert "no_vig_is_not_a_true_probability" in matched.limitations

    missing = _price(0.55, 0.40, 0.05, opposite=None)
    assert missing.ev_per_unit == 0.1
    assert missing.no_vig_probability is None
    assert missing.edge_benchmark == "raw_implied"

    wrong_line = _price(0.55, 0.40, 0.05, opposite=_opposite(line=25.5))
    wrong_book = _price(0.55, 0.40, 0.05, opposite=_opposite(book="other-book"))
    wrong_time = _price(
        0.55,
        0.40,
        0.05,
        opposite=_opposite(captured_at=KNOWN + timedelta(minutes=5)),
    )
    assert wrong_line.pair_status == "rejected"
    assert wrong_line.pair_reason == "line_mismatch"
    assert wrong_line.no_vig_probability is None
    assert wrong_line.ev_per_unit == 0.1
    assert wrong_line.edge_benchmark == "raw_implied"
    assert wrong_book.pair_reason == "book_mismatch"
    assert wrong_time.pair_reason == "timestamp_mismatch"
    assert wrong_book.ev_per_unit == 0.1
    assert wrong_time.ev_per_unit == 0.1


def test_identity_and_cutoff_failures_store_no_price():
    other_player = _price(0.55, 0.40, 0.05, quote=_quote(canonical_player_id="player:synthetic-other"))
    later = CUTOFF + timedelta(minutes=1)
    future = price_quote(
        _comparison(0.55, 0.40, 0.05, quote_captured_at=later, quote_available_at=later),
        _quote(captured_at=later, available_at=later),
        decision_cutoff=CUTOFF,
    )
    assert other_player.status == "unavailable"
    assert other_player.unavailable_reason == "player_mismatch"
    assert other_player.ev_per_unit is None
    assert other_player.probability_edge is None
    assert other_player.decimal_price is None
    assert future.unavailable_reason == "future_evidence"
    assert future.ev_per_unit is None
    assert future.recommendations_enabled is False


def test_mean_minus_line_stays_separate_and_empty_when_unresolved():
    unresolved = _price(0.55, 0.40, 0.05, distribution_mean=None)
    assert unresolved.mean_minus_line is None
    assert unresolved.probability_edge is not None
    resolved = _price(0.55, 0.40, 0.05, distribution_mean=30)
    assert resolved.mean_minus_line == 5.5
    assert resolved.probability_edge != resolved.mean_minus_line
    assert "mean_minus_line_is_not_probability_edge" in resolved.limitations


def test_pipeline_prices_the_quote_and_does_not_recommend():
    distribution = _distribution(
        tuple(0.75 if stat == 30 else 0.25 if stat == 20 else 0.0 for stat in range(31)),
        player="player:synthetic-guard-a",
        game="game:synthetic-2026-11-01-home-away",
    )
    payload = load_json("synthetic_framework.json")
    subject = load_subject("valid_points.json")
    opposite = MarketSnapshot(
        snapshot_id="snapshot:synthetic-points-under",
        canonical_game_id=subject.canonical_game_id,
        canonical_player_id=subject.canonical_player_id,
        raw_player_name="Synthetic Guard A",
        market=Market.POINTS,
        side="under",
        line=24.5,
        american_odds="-110",
        book="synthetic-book",
        captured_at=subject.snapshot.captured_at,
        available_at=subject.snapshot.available_at,
        source_ids=subject.snapshot.source_ids,
    )
    bundle = PipelineInput(
        subject=subject,
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
        log_snapshot_ids=(payload["log_snapshot_id"],),
        residual_distribution=distribution,
        distribution_available_at=datetime(2026, 11, 1, 22, 50, tzinfo=timezone.utc),
        opposite_quote=opposite,
    )
    report = run_pipeline(bundle, catalog())
    price = report.price
    assert price is not None
    assert price.status == "available"
    assert price.book == "synthetic-book"
    assert price.american_odds == "-110"
    assert price.odds_snapshot_id == "snapshot:synthetic-points-1"
    assert price.opposite_snapshot_id == "snapshot:synthetic-points-under"
    assert price.no_vig_probability == 0.5
    assert price.no_vig_is_true_probability is False
    assert price.ev_per_unit == float(Fraction(19, 44))
    assert price.mean_minus_line == 3.0
    assert price.probability_edge != price.mean_minus_line
    assert price.edge_benchmark == "no_vig"
    assert report.comparison.implied_probability is None
    assert report.decision.result == "pass"
    assert report.decision.reason == "not_implemented"
    assert report.decision.edge is None
    assert report.decision.ev_per_unit is None
    assert report.decision.threshold is None
    assert report.decision.stake is None
    assert report.decision.live_recommendation is False
    assert report.recommendation == "none"
    assert priced_quote_from_input(MarketQuoteInput.model_validate(subject.snapshot.model_dump() | {"american_odds": None})) is None
