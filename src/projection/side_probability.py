"""Over and under probabilities from one integer residual histogram.

For a nonnegative integer outcome X and an unrounded line L:

- Over wins when X > L, loses when X < L, and pushes when X = L.
- Under wins when X < L, loses when X > L, and pushes when X = L.

Comparisons use the line as given. A non-integer line, including a half-point
line, matches no integer, so its push probability is zero.

Integer bins through support_max are classified one by one. The tail bin is
every integer strictly above support_max, and those integers have no separate
probabilities. That mass is added to one class only when every such integer
falls in that class, which is true when L < support_max + 1. A larger line
puts some tail integers on different sides of the line, or on the line itself.
The result is then unavailable. The resolved bins are not renormalized, and
the tail is not spread across win, loss, and push.

Fixed minutes are the sample condition already stored on the histogram. They
are not a distribution of minutes. Positive smoothing adds a pseudocount to
the tail as well as to every integer, so a line that worked with an empty
tail can become unavailable, and exact log score and CRPS stay unavailable
for that unresolved tail.

The result is experimental and uncalibrated. It does not select American
odds, an implied probability, a no-vig price, edge, or expected value.
"""

import math
from datetime import datetime

from contracts.common import Market, Side, SourceId
from contracts.comparison import MarketComparison
from contracts.integer_residual import RESIDUAL_VERSION, IntegerResidualDistribution
from contracts.stages import CalibrationStatus, StageState, ValueOrigin

_TAIL_DUST = 1e-12


def side_probabilities(
    distribution: IntegerResidualDistribution,
    *,
    canonical_player_id: str,
    canonical_game_id: str,
    market: Market,
    line: float | None,
    side: str | None,
    odds_snapshot_id: str,
    quote_captured_at: datetime,
    quote_available_at: datetime,
    distribution_available_at: datetime | None,
    decision_cutoff: datetime,
    rules_version: str,
    source_ids: tuple[SourceId, ...] = (),
    quote_player_id: str | None = None,
    quote_game_id: str | None = None,
    quote_market: str | None = None,
) -> MarketComparison:
    """Classify the histogram at one line. This does not price the quote."""

    reason = _refusal(
        distribution,
        canonical_player_id=canonical_player_id,
        canonical_game_id=canonical_game_id,
        market=market,
        line=line,
        side=side,
        quote_captured_at=quote_captured_at,
        quote_available_at=quote_available_at,
        distribution_available_at=distribution_available_at,
        decision_cutoff=decision_cutoff,
        quote_player_id=quote_player_id,
        quote_game_id=quote_game_id,
        quote_market=quote_market,
    )
    stored_side = side if side in {item.value for item in Side} else None
    stored_line = line if _valid_line(line) else None
    if reason is not None:
        return _comparison(
            distribution,
            canonical_player_id=canonical_player_id,
            canonical_game_id=canonical_game_id,
            market=market,
            line=stored_line,
            side=stored_side,
            odds_snapshot_id=odds_snapshot_id,
            quote_captured_at=quote_captured_at,
            quote_available_at=quote_available_at,
            rules_version=rules_version,
            source_ids=source_ids,
            stage_state=StageState.UNAVAILABLE,
            unavailable_reason=reason,
            probabilities=None,
        )
    assert stored_line is not None and stored_side is not None
    probabilities = _classify(distribution, stored_line, stored_side)
    if probabilities is None:
        return _comparison(
            distribution,
            canonical_player_id=canonical_player_id,
            canonical_game_id=canonical_game_id,
            market=market,
            line=stored_line,
            side=stored_side,
            odds_snapshot_id=odds_snapshot_id,
            quote_captured_at=quote_captured_at,
            quote_available_at=quote_available_at,
            rules_version=rules_version,
            source_ids=source_ids,
            stage_state=StageState.UNAVAILABLE,
            unavailable_reason="tail_split_by_line",
            probabilities=None,
        )
    win, loss, push = probabilities
    if not _unit_mass(win, loss, push):
        return _comparison(
            distribution,
            canonical_player_id=canonical_player_id,
            canonical_game_id=canonical_game_id,
            market=market,
            line=stored_line,
            side=stored_side,
            odds_snapshot_id=odds_snapshot_id,
            quote_captured_at=quote_captured_at,
            quote_available_at=quote_available_at,
            rules_version=rules_version,
            source_ids=source_ids,
            stage_state=StageState.UNAVAILABLE,
            unavailable_reason="probability_not_normalized",
            probabilities=None,
        )
    return _comparison(
        distribution,
        canonical_player_id=canonical_player_id,
        canonical_game_id=canonical_game_id,
        market=market,
        line=stored_line,
        side=stored_side,
        odds_snapshot_id=odds_snapshot_id,
        quote_captured_at=quote_captured_at,
        quote_available_at=quote_available_at,
        rules_version=rules_version,
        source_ids=source_ids,
        stage_state=StageState.CALCULATED,
        unavailable_reason=None,
        probabilities=(win, loss, push),
    )


def _refusal(
    distribution: IntegerResidualDistribution,
    *,
    canonical_player_id: str,
    canonical_game_id: str,
    market: Market,
    line: float | None,
    side: str | None,
    quote_captured_at: datetime,
    quote_available_at: datetime,
    distribution_available_at: datetime | None,
    decision_cutoff: datetime,
    quote_player_id: str | None,
    quote_game_id: str | None,
    quote_market: str | None,
) -> str | None:
    if distribution.canonical_player_id != canonical_player_id:
        return "player_mismatch"
    if quote_player_id is not None and quote_player_id != canonical_player_id:
        return "player_mismatch"
    if distribution.canonical_game_id != canonical_game_id:
        return "game_mismatch"
    if quote_game_id is not None and quote_game_id != canonical_game_id:
        return "game_mismatch"
    if distribution.market is not market:
        return "market_mismatch"
    if quote_market is not None and quote_market != market.value:
        return "market_mismatch"
    if distribution_available_at is None:
        return "missing_distribution_clock"
    if (
        distribution_available_at > decision_cutoff
        or quote_available_at > decision_cutoff
        or quote_captured_at > decision_cutoff
    ):
        return "future_evidence"
    if side not in {item.value for item in Side}:
        return "invalid_side"
    if not _valid_line(line):
        return "invalid_line"
    return None


def _valid_line(line: float | None) -> bool:
    return isinstance(line, (int, float)) and not isinstance(line, bool) and math.isfinite(line) and line > 0


def _classify(
    distribution: IntegerResidualDistribution, line: float, side: str
) -> tuple[float, float, float] | None:
    win = 0.0
    loss = 0.0
    push = 0.0
    for item in distribution.outcomes:
        win, loss, push = _add(win, loss, push, side, _bucket(item.stat, line), item.probability)
    if distribution.tail_probability > _TAIL_DUST:
        if not line < distribution.support_max + 1:
            return None
        win, loss, push = _add(win, loss, push, side, "gt", distribution.tail_probability)
    return win, loss, push


def _bucket(stat: int, line: float) -> str:
    if stat > line:
        return "gt"
    if stat < line:
        return "lt"
    return "eq"


def _add(win: float, loss: float, push: float, side: str, bucket: str, mass: float) -> tuple[float, float, float]:
    if side == Side.OVER.value:
        if bucket == "gt":
            return win + mass, loss, push
        if bucket == "lt":
            return win, loss + mass, push
        return win, loss, push + mass
    if bucket == "lt":
        return win + mass, loss, push
    if bucket == "gt":
        return win, loss + mass, push
    return win, loss, push + mass


def _unit_mass(win: float, loss: float, push: float) -> bool:
    return all(math.isfinite(value) and value >= 0 for value in (win, loss, push)) and abs(win + loss + push - 1) <= 1e-9


def _comparison(
    distribution: IntegerResidualDistribution,
    *,
    canonical_player_id: str,
    canonical_game_id: str,
    market: Market,
    line: float | None,
    side: str | None,
    odds_snapshot_id: str,
    quote_captured_at: datetime,
    quote_available_at: datetime,
    rules_version: str,
    source_ids: tuple[SourceId, ...],
    stage_state: StageState,
    unavailable_reason: str | None,
    probabilities: tuple[float, float, float] | None,
) -> MarketComparison:
    label = side if side is not None else "refused"
    win = loss = push = None
    if probabilities is not None:
        win, loss, push = probabilities
    return MarketComparison(
        comparison_id=f"comparison:residual-{market.value}-{label}",
        canonical_projection_id=distribution.distribution_id,
        canonical_player_id=canonical_player_id,
        canonical_game_id=canonical_game_id,
        market=market,
        line=line,
        side=side,
        odds_snapshot_id=odds_snapshot_id,
        p_win=win,
        p_loss=loss,
        p_push=push,
        quote_captured_at=quote_captured_at,
        quote_available_at=quote_available_at,
        rules_version=rules_version,
        model_version=RESIDUAL_VERSION,
        stage_state=stage_state,
        origin=ValueOrigin.OBSERVED,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        unavailable_reason=unavailable_reason,
        experimental=True,
        minutes_mode=distribution.minutes_mode,
        minutes_tolerance=distribution.minutes_tolerance,
        smoothing=distribution.smoothing,
        limitations=_limitations(distribution),
        source_ids=_sources(distribution, source_ids),
    )


def _limitations(distribution: IntegerResidualDistribution) -> tuple[str, ...]:
    extra: list[str] = []
    if distribution.minutes_mode == "fixed" and distribution.minutes_tolerance == 0:
        extra.append("exact_minutes_sample")
    if distribution.smoothing > 0:
        extra.append("positive_smoothing_adds_unresolved_tail_mass")
    ordered: list[str] = []
    for item in (*distribution.limitations, *extra):
        if item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def _sources(distribution: IntegerResidualDistribution, extra: tuple[SourceId, ...]) -> tuple[SourceId, ...]:
    found: list[SourceId] = []
    seen: set[tuple[str, str]] = set()
    for item in (*distribution.source_ids, *extra):
        key = (item.provider_id, item.native_id)
        if key in seen:
            continue
        seen.add(key)
        found.append(item)
    return tuple(found)
