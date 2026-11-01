"""Map one Odds API event-odds payload into MarketSnapshot records.

Provider field names stay in this module. Player ids come only from an
explicit name map. A reviewed identity also carries team, season, and the
time that evidence was retrieved. Matching is exact after case and whitespace
normalization, or an explicit alias. A missing market stays missing. Over and
under are stored only when they share the player, event, stat, line,
sportsbook, and source update time. captured_at is that source update.
available_at is the retrieval time. Later identity evidence does not rewrite
those clocks and does not bypass the earlier-tip gate.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Literal, Protocol

from pydantic import Field, ValidationError, field_validator

from contracts.common import (
    AMERICAN_ODDS,
    PLAYER_ID,
    SNAPSHOT_ID,
    UTCDateTime,
    AnalysisState,
    GameId,
    Market,
    SourceId,
    StrictModel,
    require_utc,
)
from contracts.configuration import OddsApiSettings, PlayerNameMapping
from contracts.market import MarketSnapshot
from ingestion.adapters.base import FetchResult, ProviderAdapter
from ingestion.adapters.nba_api_schedule import GameDirectory
from ingestion.adapters.odds_api_client import (
    EVENT_LIST_DOCUMENTED_COST,
    PLAYER_MARKETS,
    RETRYABLE_ERRORS,
    event_odds_max_cost,
    get_json,
    redact_sensitive,
)

PROVIDER_ID = "odds-api"
ENDPOINT = "event-odds"
SPORT = "basketball_nba"
CACHE_VERSION = 1
EVENT_ID = re.compile(r"^[a-f0-9]{32}$")
BOOK_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,40}$")
MARKET_FOR = {
    "player_points": Market.POINTS,
    "player_assists": Market.ASSISTS,
    "player_rebounds": Market.REBOUNDS,
}
MARKET_CODE = {"player_points": "pts", "player_assists": "ast", "player_rebounds": "reb"}
MATCH_METHOD = "home_away_season_within_tip_tolerance"


class QuotaReport(StrictModel):
    remaining: int | None = None
    used: int | None = None
    last_cost: int | None = None


class QuarantinedQuote(StrictModel):
    reason: str
    event_id: str | None = None
    detail: str | None = None
    market: str | None = None
    identity_available_at: UTCDateTime | None = None


class EventMatch(StrictModel):
    """Both provider tip times stay intact. Neither clock replaces the other."""

    method: Literal["home_away_season_within_tip_tolerance"]
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    schedule_tip_at: UTCDateTime
    odds_commence_at: UTCDateTime
    tip_delta_seconds: int
    canonical_game_id: GameId


class RetrievedQuote(StrictModel):
    record: MarketSnapshot
    event_match: EventMatch
    identity_available_at: UTCDateTime | None = None


class OddsFetch(StrictModel):
    state: AnalysisState
    reason: str | None = None
    detail: str | None = None
    retrieved_at: UTCDateTime | None = None
    documented_max_cost: int | None = None
    quota: QuotaReport | None = None
    event_match: EventMatch | None = None
    records: tuple[RetrievedQuote, ...] = ()
    quarantined: tuple[QuarantinedQuote, ...] = ()

    @field_validator("detail")
    @classmethod
    def no_api_key_query(cls, value: str | None) -> str | None:
        if value is not None and re.search(r"(?i)apiKey=", value):
            raise ValueError("detail must not contain an api key")
        return value


class ListedEvent(StrictModel):
    event_id: str = Field(pattern=EVENT_ID.pattern)
    home_team: str = Field(min_length=1)
    away_team: str = Field(min_length=1)
    commence_time: UTCDateTime


class EventListFetch(StrictModel):
    state: AnalysisState
    reason: str | None = None
    detail: str | None = None
    retrieved_at: UTCDateTime | None = None
    documented_cost: int = EVENT_LIST_DOCUMENTED_COST
    quota: QuotaReport | None = None
    events: tuple[ListedEvent, ...] = ()
    quarantined: tuple[QuarantinedQuote, ...] = ()

    @field_validator("detail")
    @classmethod
    def no_api_key_query(cls, value: str | None) -> str | None:
        if value is not None and re.search(r"(?i)apiKey=", value):
            raise ValueError("detail must not contain an api key")
        return value

    @field_validator("documented_cost")
    @classmethod
    def events_are_free(cls, value: int) -> int:
        if value != EVENT_LIST_DOCUMENTED_COST:
            raise ValueError("the events endpoint cost is fixed")
        return value


class OddsTransport(Protocol):
    def __call__(
        self,
        *,
        endpoint: str,
        event_id: str | None,
        region: str,
        markets: tuple[str, ...],
        bookmakers: tuple[str, ...],
        timeout: float,
        api_key: str,
    ) -> dict[str, object]:
        """Return status, payload, headers, and an optional reason."""


class _Leg:
    def __init__(
        self,
        *,
        book: str,
        market_key: str,
        side: str,
        line: float,
        american: str,
        player_name: str,
        updated_at: datetime,
    ) -> None:
        self.book = book
        self.market_key = market_key
        self.side = side
        self.line = line
        self.american = american
        self.player_name = player_name
        self.updated_at = updated_at


def api_key_from_environment() -> str:
    """Return THE_ODDS_API_KEY from the process environment. Never reads a file."""

    value = os.environ.get("THE_ODDS_API_KEY", "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def normalize_person(value: str) -> str:
    return " ".join(value.split()).casefold()


def line_token(line: float) -> str:
    text = f"{line:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", "-")


def format_american(price: object) -> str | None:
    if isinstance(price, bool):
        return None
    if isinstance(price, float):
        if not price.is_integer():
            return None
        price = int(price)
    if not isinstance(price, int):
        return None
    if price >= 100:
        text = f"+{price}"
    elif price <= -100:
        text = str(price)
    else:
        return None
    if AMERICAN_ODDS.fullmatch(text) is None:
        return None
    return text


def index_identities(
    records: tuple[PlayerNameMapping, ...],
) -> dict[str, PlayerNameMapping] | str:
    index: dict[str, PlayerNameMapping] = {}
    teams: dict[str, str] = {}
    for record in records:
        prior_team = teams.get(record.canonical_player_id)
        if prior_team is not None and prior_team != record.canonical_team_id:
            return "conflicting_player_identity"
        teams[record.canonical_player_id] = record.canonical_team_id
        for raw in (record.provider_name, *record.aliases):
            key = normalize_person(raw)
            if key == "":
                return "invalid_player_map"
            current = index.get(key)
            if current is not None and current.canonical_player_id != record.canonical_player_id:
                return "ambiguous_player_name"
            if current is not None and current.canonical_team_id != record.canonical_team_id:
                return "conflicting_player_identity"
            index[key] = record
    return index


def index_names(names: dict[str, str], *, kind: str) -> dict[str, str] | str:
    index: dict[str, str] = {}
    pattern = PLAYER_ID if kind == "player" else re.compile(r"^\d{10}$")
    for raw_name, native in names.items():
        if not isinstance(raw_name, str) or normalize_person(raw_name) == "":
            return "invalid_player_map" if kind == "player" else "invalid_team_map"
        key = normalize_person(raw_name)
        if not isinstance(native, str) or pattern.fullmatch(native) is None:
            return "invalid_player_map" if kind == "player" else "invalid_team_map"
        if key in index and index[key] != native:
            return "ambiguous_player_name" if kind == "player" else "ambiguous_team"
        index[key] = native
    return index


def parse_quota(headers: object) -> QuotaReport:
    mapped = headers if isinstance(headers, dict) else {}

    def one(name: str) -> int | None:
        raw = mapped.get(name)
        if not isinstance(raw, str) or re.fullmatch(r"-?\d+", raw.strip()) is None:
            return None
        return int(raw.strip())

    return QuotaReport(
        remaining=one("x-requests-remaining"),
        used=one("x-requests-used"),
        last_cost=one("x-requests-last"),
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    try:
        return require_utc(parsed)
    except (TypeError, ValueError):
        return None


def _team_id(index: dict[str, str], name: object) -> str | None:
    if not isinstance(name, str):
        return None
    native = index.get(normalize_person(name))
    if native is None:
        return None
    return f"team:{native}"


def _snapshot_id(event_id: str, book: str, market_key: str, side: str, line: float) -> str | None:
    token = f"odds-{event_id}-{book.replace('_', '-')}-{MARKET_CODE[market_key]}-{side}-{line_token(line)}"
    text = f"snapshot:{token}"
    if SNAPSHOT_ID.fullmatch(text) is None:
        return None
    return text


def normalize_event_odds(
    payload: object,
    *,
    event_id: str,
    directory: GameDirectory,
    players: dict[str, str],
    team_names: dict[str, str],
    retrieved_at: datetime,
    max_quote_age_seconds: int,
    bookmakers: tuple[str, ...] = (),
    tip_tolerance_seconds: int = 0,
    identities: tuple[PlayerNameMapping, ...] | None = None,
) -> tuple[tuple[RetrievedQuote, ...], tuple[QuarantinedQuote, ...], EventMatch | None]:
    identity_index: dict[str, PlayerNameMapping] | None = None
    if identities is not None:
        indexed = index_identities(identities)
        if isinstance(indexed, str):
            return (), (QuarantinedQuote(reason=indexed, event_id=event_id),), None
        identity_index = indexed
        player_index: dict[str, str] | str = {}
    else:
        player_index = index_names(players, kind="player")
    team_lookup = index_names(team_names, kind="team")
    if isinstance(player_index, str):
        return (), (QuarantinedQuote(reason=player_index, event_id=event_id),), None
    if isinstance(team_lookup, str):
        return (), (QuarantinedQuote(reason=team_lookup, event_id=event_id),), None
    if not isinstance(payload, dict):
        return (), (QuarantinedQuote(reason="malformed_payload", event_id=event_id),), None

    home_name = payload.get("home_team")
    away_name = payload.get("away_team")
    home_id = _team_id(team_lookup, home_name)
    away_id = _team_id(team_lookup, away_name)
    if home_id is None or away_id is None:
        return (), (QuarantinedQuote(reason="unknown_team", event_id=event_id, detail="schedule team name"),), None
    if payload.get("id") != event_id or payload.get("sport_key") != SPORT:
        return (), (QuarantinedQuote(reason="event_mismatch", event_id=event_id),), None
    commence = _parse_time(payload.get("commence_time"))
    if commence is None:
        return (), (QuarantinedQuote(reason="malformed_timestamp", event_id=event_id, detail="commence_time"),), None

    def in_window(game: object) -> bool:
        tip_at = getattr(game, "tip_at")
        return abs((tip_at - commence).total_seconds()) <= tip_tolerance_seconds

    same_season = [
        game
        for game in directory.games.values()
        if game.home_team_id == home_id
        and game.away_team_id == away_id
        and game.record.season == directory.season
        and in_window(game)
    ]
    if len(same_season) > 1:
        return (), (QuarantinedQuote(reason="ambiguous_event", event_id=event_id),), None
    if len(same_season) == 0:
        reversed_sides = [
            game
            for game in directory.games.values()
            if game.home_team_id == away_id
            and game.away_team_id == home_id
            and game.record.season == directory.season
            and in_window(game)
        ]
        if reversed_sides:
            return (), (QuarantinedQuote(reason="reversed_home_away", event_id=event_id),), None
        other_season = [
            game
            for game in directory.games.values()
            if game.home_team_id == home_id
            and game.away_team_id == away_id
            and game.record.season != directory.season
            and in_window(game)
        ]
        reason = "season_mismatch" if other_season else "event_mismatch"
        return (), (QuarantinedQuote(reason=reason, event_id=event_id),), None
    game = same_season[0]
    event_match = EventMatch(
        method=MATCH_METHOD,
        season=directory.season,
        schedule_tip_at=game.tip_at,
        odds_commence_at=commence,
        tip_delta_seconds=int(round((commence - game.tip_at).total_seconds())),
        canonical_game_id=game.canonical_game_id,
    )
    earlier_tip = game.tip_at if game.tip_at <= commence else commence
    if earlier_tip <= retrieved_at:
        return (), (QuarantinedQuote(reason="post_tip", event_id=event_id),), event_match

    selected = bookmakers
    legs: list[_Leg] = []
    quarantined: list[QuarantinedQuote] = []
    present: set[str] = set()
    books = payload.get("bookmakers")
    if not isinstance(books, list):
        return (), (QuarantinedQuote(reason="malformed_payload", event_id=event_id, detail="bookmakers"),), event_match
    for book_row in books:
        if not isinstance(book_row, dict):
            quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id, detail="bookmaker"))
            continue
        book = book_row.get("key")
        if not isinstance(book, str) or BOOK_KEY.fullmatch(book) is None:
            quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id, detail="book"))
            continue
        if selected and book not in selected:
            continue
        markets = book_row.get("markets")
        if not isinstance(markets, list):
            quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id, detail=book))
            continue
        for market_row in markets:
            if not isinstance(market_row, dict):
                quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id, detail=book))
                continue
            market_key = market_row.get("key")
            if market_key not in MARKET_FOR:
                continue
            present.add(str(market_key))
            updated = _parse_time(market_row.get("last_update"))
            if updated is None:
                quarantined.append(
                    QuarantinedQuote(reason="malformed_timestamp", event_id=event_id, detail=str(market_key))
                )
                continue
            outcomes = market_row.get("outcomes")
            if not isinstance(outcomes, list):
                quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id, detail=str(market_key)))
                continue
            for outcome in outcomes:
                leg = _leg_from_outcome(outcome, book=book, market_key=str(market_key), updated_at=updated)
                if isinstance(leg, str):
                    quarantined.append(QuarantinedQuote(reason=leg, event_id=event_id, detail=f"{book}:{market_key}"))
                    continue
                legs.append(leg)

    for market_key in PLAYER_MARKETS:
        if market_key not in present:
            quarantined.append(QuarantinedQuote(reason="missing_market", event_id=event_id, detail=market_key))

    grouped: dict[tuple[str, str, str, str], dict[str, list[_Leg]]] = {}
    for leg in legs:
        key = (leg.book, leg.market_key, normalize_person(leg.player_name), line_token(leg.line))
        slot = grouped.setdefault(key, {"over": [], "under": []})
        slot[leg.side].append(leg)

    by_player: dict[tuple[str, str, str], list[tuple[tuple[str, str, str, str], dict[str, list[_Leg]]]]] = defaultdict(list)
    for key, slot in grouped.items():
        by_player[(key[0], key[1], key[2])].append((key, slot))

    records: list[RetrievedQuote] = []
    seen_snapshots: set[str] = set()
    schedule_source = game.record.source_ids[0]
    for player_key, items in by_player.items():
        complete: list[tuple[_Leg, _Leg]] = []
        incomplete = 0
        for _key, slot in items:
            if len(slot["over"]) > 1 or len(slot["under"]) > 1:
                quarantined.append(
                    QuarantinedQuote(
                        reason="duplicate_outcome", event_id=event_id, detail=player_key[2], market=player_key[1]
                    )
                )
                continue
            if len(slot["over"]) == 1 and len(slot["under"]) == 1:
                complete.append((slot["over"][0], slot["under"][0]))
            else:
                incomplete += 1
        if incomplete and len(items) > 1:
            quarantined.append(
                QuarantinedQuote(
                    reason="mismatched_line", event_id=event_id, detail=player_key[2], market=player_key[1]
                )
            )
        elif incomplete:
            quarantined.append(
                QuarantinedQuote(reason="unpaired_side", event_id=event_id, detail=player_key[2], market=player_key[1])
            )
        for over, under in complete:
            if over.updated_at != under.updated_at:
                quarantined.append(
                    QuarantinedQuote(
                        reason="incompatible_timestamps",
                        event_id=event_id,
                        detail=player_key[2],
                        market=player_key[1],
                    )
                )
                continue
            updated = over.updated_at
            if updated > retrieved_at:
                quarantined.append(
                    QuarantinedQuote(reason="future_dated", event_id=event_id, detail=player_key[2], market=player_key[1])
                )
                continue
            if (retrieved_at - updated).total_seconds() > max_quote_age_seconds:
                quarantined.append(
                    QuarantinedQuote(reason="stale", event_id=event_id, detail=player_key[2], market=player_key[1])
                )
                continue
            identity_available_at = None
            if identity_index is not None:
                identity = identity_index.get(player_key[2])
                if identity is None:
                    quarantined.append(
                        QuarantinedQuote(
                            reason="unresolved_player_name",
                            event_id=event_id,
                            detail=over.player_name,
                            market=player_key[1],
                        )
                    )
                    continue
                if identity.season != directory.season:
                    quarantined.append(
                        QuarantinedQuote(
                            reason="season_mismatch",
                            event_id=event_id,
                            detail=over.player_name,
                            market=player_key[1],
                            identity_available_at=identity.available_at,
                        )
                    )
                    continue
                if identity.canonical_team_id not in {game.home_team_id, game.away_team_id}:
                    quarantined.append(
                        QuarantinedQuote(
                            reason="wrong_team",
                            event_id=event_id,
                            detail=over.player_name,
                            market=player_key[1],
                            identity_available_at=identity.available_at,
                        )
                    )
                    continue
                if identity.available_at > retrieved_at:
                    quarantined.append(
                        QuarantinedQuote(
                            reason="identity_not_yet_available",
                            event_id=event_id,
                            detail=over.player_name,
                            market=player_key[1],
                            identity_available_at=identity.available_at,
                        )
                    )
                    continue
                player_id = identity.canonical_player_id
                identity_available_at = identity.available_at
            else:
                player_id = player_index.get(player_key[2])
                if player_id is None:
                    quarantined.append(
                        QuarantinedQuote(
                            reason="unresolved_player_name",
                            event_id=event_id,
                            detail=over.player_name,
                            market=player_key[1],
                        )
                    )
                    continue
            for leg in (over, under):
                quote = _quote(
                    leg,
                    event_id=event_id,
                    player_id=player_id,
                    game_id=game.canonical_game_id,
                    schedule_source=schedule_source,
                    native_game_id=game.native_game_id,
                    retrieved_at=retrieved_at,
                    event_match=event_match,
                    identity_available_at=identity_available_at,
                )
                if isinstance(quote, str):
                    quarantined.append(QuarantinedQuote(reason=quote, event_id=event_id, detail=leg.player_name))
                    continue
                if quote.record.snapshot_id in seen_snapshots:
                    quarantined.append(QuarantinedQuote(reason="duplicate_snapshot", event_id=event_id))
                    continue
                seen_snapshots.add(quote.record.snapshot_id)
                records.append(quote)

    records.sort(key=lambda item: (item.record.book, item.record.market.value, item.record.raw_player_name, item.record.line, item.record.side.value))
    return tuple(records), tuple(quarantined), event_match


def _leg_from_outcome(outcome: object, *, book: str, market_key: str, updated_at: datetime) -> _Leg | str:
    if not isinstance(outcome, dict):
        return "malformed_payload"
    side_name = outcome.get("name")
    if side_name not in {"Over", "Under"}:
        return "malformed_payload"
    description = outcome.get("description")
    if not isinstance(description, str) or normalize_person(description) == "":
        return "malformed_payload"
    point = outcome.get("point")
    if isinstance(point, bool) or not isinstance(point, (int, float)):
        return "invalid_odds"
    line = float(point)
    if line <= 0 or line != line or line in {float("inf"), float("-inf")}:
        return "invalid_odds"
    american = format_american(outcome.get("price"))
    if american is None:
        return "invalid_odds"
    return _Leg(
        book=book,
        market_key=market_key,
        side=side_name.casefold(),
        line=line,
        american=american,
        player_name=description.strip(),
        updated_at=updated_at,
    )


def _quote(
    leg: _Leg,
    *,
    event_id: str,
    player_id: str,
    game_id: str,
    schedule_source: SourceId,
    native_game_id: str,
    retrieved_at: datetime,
    event_match: EventMatch,
    identity_available_at: datetime | None = None,
) -> RetrievedQuote | str:
    snapshot_id = _snapshot_id(event_id, leg.book, leg.market_key, leg.side, leg.line)
    if snapshot_id is None:
        return "malformed_payload"
    native = f"{event_id}:{leg.book}:{leg.market_key}:{normalize_person(leg.player_name)}:{leg.side}:{line_token(leg.line)}"
    try:
        record = MarketSnapshot(
            snapshot_id=snapshot_id,
            canonical_game_id=game_id,
            canonical_player_id=player_id,
            raw_player_name=leg.player_name,
            market=MARKET_FOR[leg.market_key],
            side=leg.side,
            line=leg.line,
            american_odds=leg.american,
            book=leg.book,
            captured_at=leg.updated_at,
            available_at=retrieved_at,
            source_ids=(
                SourceId(provider_id=PROVIDER_ID, native_id=native),
                SourceId(provider_id=schedule_source.provider_id, native_id=native_game_id),
            ),
        )
    except ValidationError:
        return "malformed_payload"
    return RetrievedQuote(
        record=record,
        event_match=event_match,
        identity_available_at=identity_available_at,
    )


def _live_transport(
    *,
    endpoint: str,
    event_id: str | None,
    region: str,
    markets: tuple[str, ...],
    bookmakers: tuple[str, ...],
    timeout: float,
    api_key: str,
) -> dict[str, object]:
    if endpoint == "events":
        path = f"/v4/sports/{SPORT}/events"
        query = {"dateFormat": "iso"}
    else:
        path = f"/v4/sports/{SPORT}/events/{event_id}/odds"
        query = {"markets": ",".join(markets), "oddsFormat": "american", "dateFormat": "iso"}
        if bookmakers:
            query["bookmakers"] = ",".join(bookmakers)
        else:
            query["regions"] = region
    response = get_json(path=path, query=query, timeout=timeout, api_key=api_key)
    return {
        "status": response.status,
        "payload": response.payload,
        "headers": response.headers,
        "reason": response.reason,
    }


class _OddsRequestError(Exception):
    def __init__(self, reason: str, detail: str, headers: dict[str, str]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.headers = headers


def _safe_detail(detail: str | None, api_key: str) -> str | None:
    if detail is None:
        return None
    cleaned = redact_sensitive(detail, api_key)
    if api_key and api_key in cleaned:
        return "redacted"
    return cleaned


def _contains_secret(payload: object, api_key: str) -> bool:
    if not api_key:
        return False
    try:
        encoded = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return True
    return api_key in encoded


class OddsApiMarketAdapter(ProviderAdapter):
    """Fetch one NBA event's player points, assists, and rebounds."""

    def __init__(
        self,
        *,
        region: str = "us",
        bookmakers: tuple[str, ...] = (),
        timeout_seconds: float = 30,
        max_retries: int = 2,
        min_interval_seconds: float = 1.0,
        cache_dir: Path | str = Path("data/cache/odds-api"),
        max_quote_age_seconds: int = 900,
        max_request_credits: int = 3,
        tip_tolerance_seconds: int = 900,
        transport: OddsTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(PROVIDER_ID, ("markets",), ("points", "assists", "rebounds"))
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be from 0 through 2")
        if not 0 <= min_interval_seconds <= 30:
            raise ValueError("min_interval_seconds must be from 0 through 30")
        if not 0 < max_quote_age_seconds <= 3600:
            raise ValueError("max_quote_age_seconds must be within (0, 3600]")
        if not 1 <= max_request_credits <= 3:
            raise ValueError("max_request_credits must be from 1 through 3")
        if not 0 <= tip_tolerance_seconds <= 900:
            raise ValueError("tip_tolerance_seconds must be from 0 through 900")
        self.region = region
        self.bookmakers = bookmakers
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.min_interval_seconds = min_interval_seconds
        self.cache_dir = Path(cache_dir)
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_request_credits = max_request_credits
        self.tip_tolerance_seconds = tip_tolerance_seconds
        self._transport = transport or _live_transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleeper or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._last_request: float | None = None

    @classmethod
    def from_settings(cls, settings: OddsApiSettings, **kwargs: object) -> "OddsApiMarketAdapter":
        values: dict[str, object] = {
            "region": settings.region,
            "bookmakers": settings.bookmakers,
            "timeout_seconds": settings.timeout_seconds,
            "max_retries": settings.max_retries,
            "min_interval_seconds": settings.min_interval_seconds,
            "cache_dir": settings.cache_dir,
            "max_quote_age_seconds": settings.max_quote_age_seconds,
            "max_request_credits": settings.max_request_credits,
            "tip_tolerance_seconds": settings.tip_tolerance_seconds,
        }
        values.update(kwargs)
        return cls(**values)

    def fetch(self, capability: str, market: str | None = None) -> FetchResult:
        decision = self.check(capability, market)
        if decision.state is not AnalysisState.AVAILABLE:
            return FetchResult(state=decision.state, reason=decision.reason, capability=capability)
        return FetchResult(
            state=AnalysisState.UNAVAILABLE,
            reason="markets_query_required",
            capability=capability,
        )

    def list_events(self, api_key: str) -> EventListFetch:
        if api_key.strip() == "":
            return EventListFetch(state=AnalysisState.UNAVAILABLE, reason="missing_api_key")
        loaded = self._read_json(self._events_path())
        if loaded is not None and _contains_secret(loaded, api_key):
            return EventListFetch(state=AnalysisState.UNAVAILABLE, reason="secret_in_payload")
        if loaded is None:
            try:
                result = self._request(endpoint="events", event_id=None, api_key=api_key)
            except _OddsRequestError as exc:
                return EventListFetch(
                    state=AnalysisState.UNAVAILABLE,
                    reason=exc.reason,
                    detail=_safe_detail(exc.detail, api_key),
                    quota=parse_quota(exc.headers),
                )
            payload = result.get("payload")
            if _contains_secret(payload, api_key):
                return EventListFetch(state=AnalysisState.UNAVAILABLE, reason="secret_in_payload", quota=parse_quota(result.get("headers")))
            retrieved_at = require_utc(self._clock())
            self._write_json(
                self._events_path(),
                {"version": CACHE_VERSION, "endpoint": "events", "retrieved_at": retrieved_at.isoformat(), "quota": _quota_dict(result.get("headers")), "payload": payload},
                api_key,
            )
        else:
            retrieved_at = _parse_time(loaded.get("retrieved_at"))
            payload = loaded.get("payload")
            if retrieved_at is None:
                return EventListFetch(state=AnalysisState.UNAVAILABLE, reason="malformed_payload")
            result = {"headers": _headers_from_quota(loaded.get("quota"))}
        events, quarantined = _normalize_events(payload)
        return EventListFetch(
            state=AnalysisState.AVAILABLE,
            retrieved_at=retrieved_at,
            quota=parse_quota(result.get("headers")),
            events=events,
            quarantined=quarantined,
        )

    def fetch_event_odds(
        self,
        event_id: str,
        directory: GameDirectory,
        players: dict[str, str],
        team_names: dict[str, str],
        api_key: str,
    ) -> OddsFetch:
        if EVENT_ID.fullmatch(event_id) is None:
            return self._refused("invalid_query", api_key)
        if api_key.strip() == "":
            return self._refused("missing_api_key", api_key)
        player_index = index_names(players, kind="player")
        team_lookup = index_names(team_names, kind="team")
        if isinstance(player_index, str):
            return self._refused(player_index, api_key, event_id=event_id)
        if isinstance(team_lookup, str):
            return self._refused(team_lookup, api_key, event_id=event_id)
        max_cost = event_odds_max_cost(
            markets=PLAYER_MARKETS,
            regions=(self.region,),
            bookmakers=self.bookmakers,
        )
        if max_cost > self.max_request_credits:
            return OddsFetch(
                state=AnalysisState.UNAVAILABLE,
                reason="credit_cap_exceeded",
                documented_max_cost=max_cost,
            )
        cached = self._read_json(self._odds_path(event_id))
        if cached is not None and _contains_secret(cached, api_key):
            return self._refused("secret_in_payload", api_key, event_id=event_id)
        if cached is None:
            try:
                result = self._request(endpoint="event-odds", event_id=event_id, api_key=api_key)
            except _OddsRequestError as exc:
                return OddsFetch(
                    state=AnalysisState.UNAVAILABLE,
                    reason=exc.reason,
                    detail=_safe_detail(exc.detail, api_key),
                    documented_max_cost=max_cost,
                    quota=parse_quota(exc.headers),
                )
            payload = result.get("payload")
            if _contains_secret(payload, api_key):
                return OddsFetch(
                    state=AnalysisState.UNAVAILABLE,
                    reason="secret_in_payload",
                    documented_max_cost=max_cost,
                    quota=parse_quota(result.get("headers")),
                )
            retrieved_at = require_utc(self._clock())
            stored = self._write_json(
                self._odds_path(event_id),
                {
                    "version": CACHE_VERSION,
                    "endpoint": ENDPOINT,
                    "event_id": event_id,
                    "region": self.region,
                    "bookmakers": list(self.bookmakers),
                    "retrieved_at": retrieved_at.isoformat(),
                    "quota": _quota_dict(result.get("headers")),
                    "payload": payload,
                },
                api_key,
            )
            if not stored:
                return self._refused("secret_in_payload", api_key, event_id=event_id)
            headers = result.get("headers")
        else:
            retrieved_at = _parse_time(cached.get("retrieved_at"))
            payload = cached.get("payload")
            if retrieved_at is None or cached.get("event_id") != event_id:
                return self._refused("malformed_payload", api_key, event_id=event_id)
            headers = _headers_from_quota(cached.get("quota"))
        records, quarantined, event_match = normalize_event_odds(
            payload,
            event_id=event_id,
            directory=directory,
            players=players,
            team_names=team_names,
            retrieved_at=retrieved_at,
            max_quote_age_seconds=self.max_quote_age_seconds,
            bookmakers=self.bookmakers,
            tip_tolerance_seconds=self.tip_tolerance_seconds,
        )
        return OddsFetch(
            state=AnalysisState.AVAILABLE,
            retrieved_at=retrieved_at,
            documented_max_cost=max_cost,
            quota=parse_quota(headers),
            event_match=event_match,
            records=records,
            quarantined=quarantined,
        )

    def _refused(self, reason: str, api_key: str, event_id: str | None = None) -> OddsFetch:
        return OddsFetch(
            state=AnalysisState.UNAVAILABLE,
            reason=reason,
            detail=_safe_detail(None, api_key),
            quarantined=(QuarantinedQuote(reason=reason, event_id=event_id),) if event_id else (),
        )

    def _request(self, *, endpoint: str, event_id: str | None, api_key: str) -> dict[str, object]:
        attempts = self.max_retries + 1
        last_name = "provider_error"
        for attempt in range(attempts):
            self._pace()
            try:
                result = self._transport(
                    endpoint=endpoint,
                    event_id=event_id,
                    region=self.region,
                    markets=PLAYER_MARKETS,
                    bookmakers=self.bookmakers,
                    timeout=self.timeout_seconds,
                    api_key=api_key,
                )
            except Exception as exc:
                last_name = type(exc).__name__
                if last_name not in RETRYABLE_ERRORS or attempt == attempts - 1:
                    reason = "provider_timeout" if last_name in RETRYABLE_ERRORS else "provider_error"
                    raise _OddsRequestError(reason, last_name, {}) from None
                continue
            if not isinstance(result, dict):
                raise _OddsRequestError("provider_error", "invalid_payload", {})
            reason = result.get("reason")
            headers = result.get("headers")
            header_map = {str(key).lower(): str(value) for key, value in headers.items()} if isinstance(headers, dict) else {}
            if isinstance(reason, str):
                raise _OddsRequestError(reason, str(result.get("status", "")), header_map) from None
            return result
        raise _OddsRequestError("provider_timeout", last_name, {})

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request is not None:
            remaining = self.min_interval_seconds - (now - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
                now = self._last_request + self.min_interval_seconds
        self._last_request = now

    def _events_path(self) -> Path:
        return self.cache_dir / "events" / f"{SPORT}.json"

    def _odds_path(self, event_id: str) -> Path:
        books = "region" if not self.bookmakers else "+".join(self.bookmakers)
        return self.cache_dir / ENDPOINT / event_id / self.region / f"{'+'.join(PLAYER_MARKETS)}--{books}.json"

    def _read_json(self, path: Path) -> dict[str, object] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return None
        return payload

    def _write_json(self, path: Path, payload: dict[str, object], api_key: str) -> bool:
        encoded = json.dumps(payload, separators=(",", ":"), default=str)
        if _contains_secret(encoded, api_key) or "apiKey=" in encoded:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
        return True


def _quota_dict(headers: object) -> dict[str, str]:
    report = parse_quota(headers)
    stored: dict[str, str] = {}
    if report.remaining is not None:
        stored["x-requests-remaining"] = str(report.remaining)
    if report.used is not None:
        stored["x-requests-used"] = str(report.used)
    if report.last_cost is not None:
        stored["x-requests-last"] = str(report.last_cost)
    return stored


def _headers_from_quota(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _normalize_events(payload: object) -> tuple[tuple[ListedEvent, ...], tuple[QuarantinedQuote, ...]]:
    if not isinstance(payload, list):
        return (), (QuarantinedQuote(reason="malformed_payload"),)
    events: list[ListedEvent] = []
    quarantined: list[QuarantinedQuote] = []
    for row in payload:
        if not isinstance(row, dict):
            quarantined.append(QuarantinedQuote(reason="malformed_payload"))
            continue
        event_id = row.get("id")
        commence = _parse_time(row.get("commence_time"))
        home = row.get("home_team")
        away = row.get("away_team")
        if not isinstance(event_id, str) or not isinstance(home, str) or not isinstance(away, str) or commence is None:
            quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id if isinstance(event_id, str) else None))
            continue
        try:
            events.append(ListedEvent(event_id=event_id, home_team=home.strip(), away_team=away.strip(), commence_time=commence))
        except ValidationError:
            quarantined.append(QuarantinedQuote(reason="malformed_payload", event_id=event_id))
    return tuple(events), tuple(quarantined)
