"""Bind one quote to one probability comparison and price that side.

The sportsbook, line, side, player, event, stat, and odds snapshot on the
quote must be the comparison's quote. A missing opposite side does not block
expected value. A mismatched opposite side blocks only the no-vig number.
Compatible opposite timestamps mean the same captured_at, with both clocks
at or before the decision cutoff.

Probability edge is conditional win probability minus the labeled benchmark.
The benchmark is the proportional no-vig probability when the pair matches,
and the raw implied probability otherwise. Distribution mean minus the line
is stored separately and is left empty when that mean is unresolved.
"""

from datetime import datetime
from fractions import Fraction

from contracts.common import Market, Side, SourceId
from contracts.comparison import MarketComparison
from contracts.market import MarketQuoteInput, MarketSnapshot
from contracts.price import PRICE_VERSION, REQUIRED_PRICE_LIMITATIONS, PriceComparison, PricedQuote
from contracts.stages import CalibrationStatus
from edge.formulas import american_price, conditional_win_probability, expected_value, proportional_no_vig


def priced_quote_from_snapshot(snapshot: MarketSnapshot) -> PricedQuote:
    return PricedQuote(
        canonical_player_id=snapshot.canonical_player_id,
        canonical_game_id=snapshot.canonical_game_id,
        market=snapshot.market,
        side=snapshot.side,
        line=snapshot.line,
        book=snapshot.book,
        american_odds=snapshot.american_odds,
        snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        available_at=snapshot.available_at,
        source_ids=snapshot.source_ids,
    )


def priced_quote_from_input(quote: MarketQuoteInput) -> PricedQuote | None:
    """Return a quote only when every pricing field is already canonical."""

    if (
        quote.canonical_player_id is None
        or quote.canonical_game_id is None
        or quote.snapshot_id is None
        or quote.side not in {item.value for item in Side}
        or quote.market not in {item.value for item in Market}
        or quote.line is None
        or quote.line <= 0
        or quote.american_odds is None
        or quote.book.strip() == ""
        or len(quote.source_ids) < 1
    ):
        return None
    try:
        return PricedQuote(
            canonical_player_id=quote.canonical_player_id,
            canonical_game_id=quote.canonical_game_id,
            market=quote.market,
            side=quote.side,
            line=quote.line,
            book=quote.book,
            american_odds=quote.american_odds,
            snapshot_id=quote.snapshot_id,
            captured_at=quote.captured_at,
            available_at=quote.available_at,
            source_ids=quote.source_ids,
        )
    except ValueError:
        return None


def price_quote(
    comparison: MarketComparison,
    quote: PricedQuote,
    *,
    decision_cutoff: datetime,
    opposite: PricedQuote | None = None,
    distribution_mean: float | None = None,
) -> PriceComparison:
    """Price the comparison's side. This does not recommend the side."""

    reason = _binding_reason(comparison, quote, decision_cutoff)
    if reason is not None:
        return refused_price(comparison, reason)
    decimal, implied = american_price(quote.american_odds)
    profit = decimal - 1
    assert comparison.p_win is not None and comparison.p_loss is not None and comparison.p_push is not None
    ev = expected_value(comparison.p_win, comparison.p_loss, profit)
    conditional = conditional_win_probability(comparison.p_win, comparison.p_loss)
    pair_status, pair_reason, no_vig, opposite_id, opposite_sources = _pair(quote, opposite, decision_cutoff)
    if conditional is None:
        benchmark = None
        edge = None
        edge_reason = "all_push"
        ev = Fraction(0)
    elif pair_status == "matched" and no_vig is not None:
        benchmark = "no_vig"
        edge = conditional - no_vig
        edge_reason = None
    else:
        benchmark = "raw_implied"
        edge = conditional - implied
        edge_reason = None
    mean_gap = None if distribution_mean is None else float(Fraction(str(distribution_mean)) - Fraction(str(quote.line)))
    return PriceComparison(
        price_id=f"price:edge-{quote.market.value}-{quote.side.value}",
        model_version=PRICE_VERSION,
        experimental=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_status="unvalidated",
        comparison_id=comparison.comparison_id,
        canonical_projection_id=comparison.canonical_projection_id,
        canonical_player_id=quote.canonical_player_id,
        canonical_game_id=quote.canonical_game_id,
        market=quote.market,
        line=quote.line,
        side=quote.side,
        book=quote.book,
        american_odds=quote.american_odds,
        odds_snapshot_id=quote.snapshot_id,
        opposite_snapshot_id=opposite_id,
        decimal_price=float(decimal),
        raw_implied_probability=float(implied),
        raw_implied_contains_vig=True,
        no_vig_probability=None if no_vig is None else float(no_vig),
        no_vig_method=None if no_vig is None else "proportional",
        p_win=comparison.p_win,
        p_loss=comparison.p_loss,
        p_push=comparison.p_push,
        conditional_win_probability=None if conditional is None else float(conditional),
        probability_edge=None if edge is None else float(edge),
        edge_benchmark=benchmark,
        edge_reason=edge_reason,
        ev_per_unit=float(ev),
        distribution_mean=distribution_mean,
        mean_minus_line=mean_gap,
        pair_status=pair_status,
        pair_reason=pair_reason,
        status="available",
        unavailable_reason=None,
        limitations=REQUIRED_PRICE_LIMITATIONS,
        source_ids=_sources(comparison.source_ids, quote.source_ids, opposite_sources),
    )


def _binding_reason(comparison: MarketComparison, quote: PricedQuote, cutoff: datetime) -> str | None:
    if comparison.canonical_player_id != quote.canonical_player_id:
        return "player_mismatch"
    if comparison.canonical_game_id != quote.canonical_game_id:
        return "game_mismatch"
    if comparison.market is not quote.market:
        return "market_mismatch"
    if comparison.line is None or comparison.line != quote.line:
        return "line_mismatch"
    if comparison.side is None or comparison.side != quote.side:
        return "side_mismatch"
    if comparison.odds_snapshot_id != quote.snapshot_id:
        return "snapshot_mismatch"
    if comparison.book is not None and comparison.book != quote.book:
        return "book_mismatch"
    if comparison.american_odds is not None and comparison.american_odds != quote.american_odds:
        return "odds_mismatch"
    if comparison.quote_captured_at != quote.captured_at or comparison.quote_available_at != quote.available_at:
        return "timestamp_mismatch"
    if quote.captured_at > cutoff or quote.available_at > cutoff:
        return "future_evidence"
    if comparison.calibration_status is not CalibrationStatus.UNCALIBRATED:
        return "not_uncalibrated"
    if comparison.p_win is None or comparison.p_loss is None or comparison.p_push is None:
        return "probabilities_unavailable"
    return None


def _pair(
    quote: PricedQuote,
    opposite: PricedQuote | None,
    cutoff: datetime,
) -> tuple[str, str | None, Fraction | None, str | None, tuple[SourceId, ...]]:
    if opposite is None:
        return "not_supplied", None, None, None, ()
    reason = _opposite_reason(quote, opposite, cutoff)
    if reason is not None:
        return "rejected", reason, None, None, ()
    _decimal, other_implied = american_price(opposite.american_odds)
    _side_decimal, side_implied = american_price(quote.american_odds)
    return "matched", None, proportional_no_vig(side_implied, other_implied), opposite.snapshot_id, opposite.source_ids


def _opposite_reason(quote: PricedQuote, opposite: PricedQuote, cutoff: datetime) -> str | None:
    if opposite.canonical_player_id != quote.canonical_player_id:
        return "player_mismatch"
    if opposite.canonical_game_id != quote.canonical_game_id:
        return "game_mismatch"
    if opposite.market is not quote.market:
        return "market_mismatch"
    if opposite.line != quote.line:
        return "line_mismatch"
    if opposite.book != quote.book:
        return "book_mismatch"
    if opposite.side == quote.side:
        return "side_not_opposite"
    if opposite.captured_at != quote.captured_at:
        return "timestamp_mismatch"
    if opposite.captured_at > cutoff or opposite.available_at > cutoff:
        return "future_evidence"
    if opposite.snapshot_id == quote.snapshot_id:
        return "snapshot_mismatch"
    return None


def refused_price(comparison: MarketComparison, reason: str) -> PriceComparison:
    return PriceComparison(
        price_id=f"price:edge-{comparison.market.value}-refused",
        model_version=PRICE_VERSION,
        experimental=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_status="unvalidated",
        comparison_id=comparison.comparison_id,
        canonical_projection_id=comparison.canonical_projection_id,
        canonical_player_id=comparison.canonical_player_id,
        canonical_game_id=comparison.canonical_game_id,
        market=comparison.market,
        line=comparison.line,
        side=comparison.side,
        odds_snapshot_id=comparison.odds_snapshot_id,
        pair_status="not_evaluated",
        status="unavailable",
        unavailable_reason=reason,
        limitations=REQUIRED_PRICE_LIMITATIONS,
        source_ids=comparison.source_ids,
    )


def _sources(*groups: tuple[SourceId, ...]) -> tuple[SourceId, ...]:
    found: list[SourceId] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group:
            key = (item.provider_id, item.native_id)
            if key in seen:
                continue
            seen.add(key)
            found.append(item)
    return tuple(found)
