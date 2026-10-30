"""Market quotes. Canonical snapshots are well formed. Raw quotes may be rejected."""

from typing import Annotated

from pydantic import AfterValidator, Field

from contracts.common import (
    AMERICAN_ODDS,
    GAME_ID,
    SNAPSHOT_ID,
    UTCDateTime,
    GameId,
    Market,
    PlayerId,
    Side,
    SnapshotId,
    SourceId,
    StrictModel,
    require_sources,
)


class MarketQuoteInput(StrictModel):
    """Provider-shaped quote before it is accepted as a canonical snapshot."""

    snapshot_id: str | None
    canonical_game_id: str | None
    canonical_player_id: str | None
    raw_player_name: str
    market: str
    side: str | None
    line: float | None = Field(default=None, allow_inf_nan=False)
    american_odds: str | None
    book: str
    captured_at: UTCDateTime
    available_at: UTCDateTime
    source_ids: tuple[SourceId, ...] = ()


class MarketSnapshot(StrictModel):
    snapshot_id: SnapshotId
    canonical_game_id: GameId
    canonical_player_id: PlayerId
    raw_player_name: str = Field(min_length=1)
    market: Market
    side: Side
    line: float = Field(gt=0, allow_inf_nan=False)
    american_odds: str = Field(pattern=AMERICAN_ODDS.pattern)
    book: str = Field(min_length=1)
    captured_at: UTCDateTime
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]


def quote_problems(quote: MarketQuoteInput, subject_market: str | None = None) -> tuple[str, ...]:
    problems: list[str] = []
    if quote.side not in {item.value for item in Side}:
        problems.append("side")
    if quote.line is None or quote.line <= 0:
        problems.append("line")
    if quote.american_odds is None or AMERICAN_ODDS.fullmatch(quote.american_odds) is None:
        problems.append("american_odds")
    if quote.market not in {item.value for item in Market}:
        problems.append("market")
    if quote.snapshot_id is None or SNAPSHOT_ID.fullmatch(quote.snapshot_id) is None:
        problems.append("snapshot_id")
    if quote.canonical_game_id is None or GAME_ID.fullmatch(quote.canonical_game_id) is None:
        problems.append("game_id")
    if quote.book.strip() == "" or quote.book != quote.book.strip():
        problems.append("book")
    if len(quote.source_ids) < 1:
        problems.append("source_ids")
    if subject_market is not None and quote.market != subject_market:
        problems.append("market_mismatch")
    return tuple(problems)
