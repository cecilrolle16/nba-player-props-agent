"""Map a PlayerGameLog payload into canonical player logs.

Provider column names stay in this module. Accepted rows become PlayerGameLog
records. available_at is the retrieval timestamp stored with the payload.
"""

from collections.abc import Callable
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Protocol

from pydantic import Field, ValidationError, field_validator

from contracts.common import (
    UTCDateTime,
    AnalysisState,
    GameId,
    PlayerId,
    SnapshotId,
    SourceId,
    StrictModel,
    TeamId,
    require_utc,
)
from contracts.configuration import NbaApiLogSettings
from contracts.player_log import PlayerGameLog
from ingestion.adapters.base import FetchResult, ProviderAdapter
from ingestion.adapters.nba_api_schedule import SEASON_PREFIX, SEASON_TYPES, GameDirectory, parse_matchup

PROVIDER_ID = "nba-api"
ENDPOINT = "playergamelog"
SEASON_TYPE = "Regular Season"
CACHE_VERSION = 1
POINT_IN_TIME_NOTE = (
    "playergamelog has no as-of parameter. A current extract cannot establish historical availability. "
    "log_retrieved_at is the log payload retrieval time. "
    "A joined record's available_at is not earlier than every required source retrieval."
)
GAME_NATIVE = re.compile(r"^\d{10}$")
MATCHUP = re.compile(r"^([A-Za-z]{2,3})\s+(?:vs\.?|@)\s+[A-Za-z]{2,3}$")
DNP_PREFIXES = ("dnp", "dnd", "nwt", "inactive")
RETRYABLE_ERRORS = frozenset(
    {"Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutError", "ConnectionError"}
)


class IdentityMap(StrictModel):
    players: dict[str, PlayerId]
    teams: dict[str, TeamId]
    games: dict[str, GameId]

    @field_validator("players", "games", mode="before")
    @classmethod
    def strip_native_ids(cls, value: object) -> object:
        return _strip_keys(value)

    @field_validator("teams", mode="before")
    @classmethod
    def normalize_team_keys(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized: dict[object, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.strip() == "":
                raise ValueError("team map keys must be non-empty strings")
            text = key.strip()
            normalized[text if text.isdigit() else text.upper()] = item
        return normalized


class PlayerLogQuery(StrictModel):
    nba_player_id: str = Field(pattern=r"^\d{1,10}$")
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    cutoff: UTCDateTime
    identity: IdentityMap


class QuarantinedLog(StrictModel):
    reason: str
    native_player_id: str | None = None
    native_game_id: str | None = None
    detail: str | None = None


class RetrievedPlayerLog(StrictModel):
    snapshot_id: SnapshotId
    schedule_snapshot_id: SnapshotId | None = None
    log_retrieved_at: UTCDateTime
    record: PlayerGameLog


class PlayerLogFetch(StrictModel):
    state: AnalysisState
    reason: str | None = None
    detail: str | None = None
    retrieved_at: UTCDateTime | None = None
    point_in_time_limited: bool = True
    point_in_time_note: str = POINT_IN_TIME_NOTE
    records: tuple[RetrievedPlayerLog, ...] = ()
    quarantined: tuple[QuarantinedLog, ...] = ()

    @field_validator("point_in_time_note")
    @classmethod
    def fixed_note(cls, value: str) -> str:
        if value != POINT_IN_TIME_NOTE:
            raise ValueError("point_in_time_note is fixed for this endpoint")
        return value

    @field_validator("point_in_time_limited")
    @classmethod
    def always_limited(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("playergamelog results are point-in-time limited")
        return value


class PlayerLogRowSource(Protocol):
    def __call__(
        self,
        *,
        player_id: str,
        season: str,
        timeout: float,
        season_type: str = "Regular Season",
    ) -> list[dict[str, object]]:
        """Return raw endpoint rows. Implementations may perform network I/O."""


def _strip_keys(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized: dict[object, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key.strip() == "":
            raise ValueError("identity map keys must be non-empty strings")
        normalized[key.strip()] = item
    return normalized


def _live_rows(
    *,
    player_id: str,
    season: str,
    timeout: float,
    season_type: str = "Regular Season",
) -> list[dict[str, object]]:
    from ingestion.adapters.nba_api_client import fetch_playergamelog_rows

    return fetch_playergamelog_rows(player_id=player_id, season=season, timeout=timeout, season_type=season_type)


def _native_digits(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip().isdigit():
        return value.strip()
    return None


def _parse_game_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]) and (len(text) == 10 or text[10:11] == "T"):
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    titled = text.title()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(titled, fmt).date()
        except ValueError:
            continue
    return None


def _parse_minutes(present: bool, value: object) -> tuple[float | None, str | None]:
    if not present or value is None:
        return None, "missing_field" if not present else "did_not_play"
    if isinstance(value, bool):
        return None, "invalid_minutes"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None, "invalid_minutes"
        if value < 0 or value == float("inf"):
            return None, "invalid_minutes"
        return float(value), None
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if text == "" or lowered.startswith(DNP_PREFIXES):
            return None, "did_not_play"
        if ":" in text:
            parts = text.split(":")
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                return None, "invalid_minutes"
            seconds = int(parts[1])
            if seconds >= 60:
                return None, "invalid_minutes"
            return int(parts[0]) + seconds / 60, None
        try:
            number = float(text)
        except ValueError:
            return None, "invalid_minutes"
        if number < 0 or number == float("inf"):
            return None, "invalid_minutes"
        return number, None
    return None, "invalid_minutes"


def _parse_count(present: bool, value: object) -> tuple[int | None, str | None]:
    if not present or value is None or isinstance(value, bool):
        return None, "missing_field"
    if isinstance(value, int):
        if value < 0:
            return None, "invalid_counting_stat"
        return value, None
    if isinstance(value, float):
        if value != value or value < 0 or not value.is_integer():
            return None, "invalid_counting_stat"
        return int(value), None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None, "missing_field"
        if text.isdigit():
            return int(text), None
        return None, "invalid_counting_stat"
    return None, "invalid_counting_stat"


def _team_key(row: dict[str, object]) -> tuple[str | None, str | None]:
    if "TEAM_ID" in row and row["TEAM_ID"] not in (None, ""):
        native = _native_digits(row["TEAM_ID"])
        if native is None:
            return None, "invalid_team_id"
        return native, None
    matchup = row.get("MATCHUP")
    if not isinstance(matchup, str):
        return None, "missing_field"
    matched = MATCHUP.fullmatch(matchup.strip())
    if matched is None:
        return None, "unresolved_team_id"
    return matched.group(1).upper(), None


def _quarantine(
    reason: str,
    native_player_id: str | None,
    native_game_id: str | None,
    detail: str | None = None,
) -> QuarantinedLog:
    return QuarantinedLog(
        reason=reason,
        native_player_id=native_player_id,
        native_game_id=native_game_id,
        detail=detail,
    )


def _directory_identity(
    row: dict[str, object],
    *,
    native_player_id: str,
    native_game_id: str,
    directory: GameDirectory,
) -> tuple[str, str, str] | QuarantinedLog:
    blocked = directory.blocked.get(native_game_id)
    if blocked is not None:
        return _quarantine(blocked, native_player_id, native_game_id, blocked)
    if not native_game_id.startswith(SEASON_PREFIX[directory.season_type]):
        return _quarantine("season_type_mismatch", native_player_id, native_game_id, native_game_id[:3])
    game = directory.games.get(native_game_id)
    if game is None:
        return _quarantine("unresolved_game_id", native_player_id, native_game_id)
    sides = parse_matchup(row.get("MATCHUP"))
    if sides is None:
        return _quarantine("unresolved_team_id", native_player_id, native_game_id, "MATCHUP")
    player_abbreviation, opponent_abbreviation, player_is_home = sides
    player_team_id = directory.abbreviations.get(player_abbreviation)
    opponent_team_id = directory.abbreviations.get(opponent_abbreviation)
    if player_team_id is None:
        return _quarantine("unknown_abbreviation", native_player_id, native_game_id, player_abbreviation)
    if opponent_team_id is None:
        return _quarantine("unknown_abbreviation", native_player_id, native_game_id, opponent_abbreviation)
    expected_player = game.home_team_id if player_is_home else game.away_team_id
    expected_opponent = game.away_team_id if player_is_home else game.home_team_id
    if player_team_id != expected_player or opponent_team_id != expected_opponent:
        detail = f"{player_abbreviation}/{opponent_abbreviation}:{game.home_abbreviation}/{game.away_abbreviation}"
        return _quarantine("team_conflict", native_player_id, native_game_id, detail)
    return player_team_id, game.canonical_game_id, game.record.snapshot_id


def normalize_playergamelog(
    rows: list[object],
    *,
    nba_player_id: str,
    identity: IdentityMap,
    cutoff: datetime,
    retrieved_at: datetime,
    game_directory: GameDirectory | None = None,
    strict_point_in_time: bool = False,
) -> tuple[tuple[RetrievedPlayerLog, ...], tuple[QuarantinedLog, ...]]:
    cutoff_date = require_utc(cutoff).date()
    retrieved_at = require_utc(retrieved_at)
    accepted: list[tuple[date, str, RetrievedPlayerLog]] = []
    quarantined: list[QuarantinedLog] = []
    seen_games: set[str] = set()
    canonical_player_id = identity.players[nba_player_id]

    for raw in rows:
        if not isinstance(raw, dict):
            quarantined.append(_quarantine("invalid_row", nba_player_id, None))
            continue
        row = {str(key): value for key, value in raw.items()}
        player_present = "Player_ID" in row
        player_native = _native_digits(row.get("Player_ID")) if player_present else None
        game_present = "Game_ID" in row
        game_native = row.get("Game_ID")
        game_text = game_native.strip() if isinstance(game_native, str) else None
        if isinstance(game_native, int) and not isinstance(game_native, bool):
            game_text = str(game_native)
        if not player_present or player_native is None:
            quarantined.append(_quarantine("missing_field", None, game_text, "Player_ID"))
            continue
        if player_native != nba_player_id:
            quarantined.append(_quarantine("player_id_mismatch", player_native, game_text))
            continue
        if not game_present or game_text is None:
            quarantined.append(_quarantine("missing_field", player_native, None, "Game_ID"))
            continue
        if GAME_NATIVE.fullmatch(game_text) is None:
            quarantined.append(_quarantine("invalid_game_id", player_native, game_text))
            continue

        minutes, minutes_reason = _parse_minutes("MIN" in row, row.get("MIN"))
        if minutes_reason is not None:
            detail = "MIN" if minutes_reason == "missing_field" else None
            quarantined.append(_quarantine(minutes_reason, player_native, game_text, detail))
            continue

        counts: dict[str, int] = {}
        count_problem: tuple[str, str] | None = None
        for column, name in (("PTS", "points"), ("AST", "assists"), ("REB", "rebounds")):
            parsed, reason = _parse_count(column in row, row.get(column))
            if reason is not None or parsed is None:
                count_problem = (reason or "missing_field", column)
                break
            counts[name] = parsed
        if count_problem is not None:
            quarantined.append(_quarantine(count_problem[0], player_native, game_text, count_problem[1]))
            continue

        game_date = _parse_game_date(row.get("GAME_DATE")) if "GAME_DATE" in row else None
        if game_date is None:
            quarantined.append(_quarantine("invalid_game_date", player_native, game_text, "GAME_DATE"))
            continue

        schedule_retrieved_at = None if game_directory is None else game_directory.retrieved_at
        schedule_snapshot_id = None
        if game_directory is not None and strict_point_in_time and cutoff < game_directory.retrieved_at:
            quarantined.append(_quarantine("schedule_not_yet_available", player_native, game_text))
            continue
        if strict_point_in_time and cutoff < retrieved_at:
            quarantined.append(_quarantine("log_not_yet_available", player_native, game_text))
            continue
        if game_directory is not None:
            resolved = _directory_identity(
                row,
                native_player_id=player_native,
                native_game_id=game_text,
                directory=game_directory,
            )
            if isinstance(resolved, QuarantinedLog):
                quarantined.append(resolved)
                continue
            canonical_team_id, canonical_game_id, schedule_snapshot_id = resolved
        else:
            team_key, team_problem = _team_key(row)
            if team_problem is not None or team_key is None:
                reason = "missing_field" if team_problem == "missing_field" else "unresolved_team_id"
                quarantined.append(_quarantine(reason, player_native, game_text, team_problem))
                continue
            canonical_team_id = identity.teams.get(team_key)
            if canonical_team_id is None:
                quarantined.append(_quarantine("unresolved_team_id", player_native, game_text, team_key))
                continue
            canonical_game_id = identity.games.get(game_text)
            if canonical_game_id is None:
                quarantined.append(_quarantine("unresolved_game_id", player_native, game_text))
                continue

        if game_date > cutoff_date:
            quarantined.append(_quarantine("after_cutoff", player_native, game_text))
            continue
        if game_date == cutoff_date:
            quarantined.append(_quarantine("cutoff_date_unresolved", player_native, game_text))
            continue
        if canonical_game_id in seen_games:
            quarantined.append(_quarantine("duplicate_record", player_native, game_text, canonical_game_id))
            continue

        snapshot_id = f"snapshot:nba-api-{game_text}"
        available_at = retrieved_at if schedule_retrieved_at is None else max(retrieved_at, schedule_retrieved_at)
        record = RetrievedPlayerLog(
            snapshot_id=snapshot_id,
            schedule_snapshot_id=schedule_snapshot_id,
            log_retrieved_at=retrieved_at,
            record=PlayerGameLog(
                canonical_player_id=canonical_player_id,
                canonical_game_id=canonical_game_id,
                canonical_team_id=canonical_team_id,
                points=counts["points"],
                assists=counts["assists"],
                rebounds=counts["rebounds"],
                minutes=minutes,
                available_at=available_at,
                source_ids=(SourceId(provider_id=PROVIDER_ID, native_id=f"{ENDPOINT}:{player_native}:{game_text}"),),
            ),
        )
        seen_games.add(canonical_game_id)
        accepted.append((game_date, game_text, record))

    accepted.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in accepted), tuple(quarantined)


def _refused(reason: str, detail: str | None = None, native_player_id: str | None = None) -> PlayerLogFetch:
    quarantined = ()
    if reason == "unresolved_player_id" and native_player_id is not None:
        quarantined = (_quarantine(reason, native_player_id, None),)
    return PlayerLogFetch(state=AnalysisState.UNAVAILABLE, reason=reason, detail=detail, quarantined=quarantined)


def _parse_cached_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return require_utc(parsed)
    except (TypeError, ValueError):
        return None


class NbaApiPlayerLogAdapter(ProviderAdapter):
    """Fetch one regular-season PlayerGameLog and normalize it locally."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        min_interval_seconds: float = 1.0,
        cache_dir: Path | str = Path("data/cache/nba-api"),
        row_source: PlayerLogRowSource | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(PROVIDER_ID, ("player_logs",))
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be from 0 through 2")
        if not 0 <= min_interval_seconds <= 30:
            raise ValueError("min_interval_seconds must be from 0 through 30")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.min_interval_seconds = min_interval_seconds
        self.cache_dir = Path(cache_dir)
        self._row_source = row_source or _live_rows
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleeper or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._last_request: float | None = None

    @classmethod
    def from_settings(cls, settings: NbaApiLogSettings, **kwargs: object) -> "NbaApiPlayerLogAdapter":
        values: dict[str, object] = {
            "timeout_seconds": settings.timeout_seconds,
            "max_retries": settings.max_retries,
            "min_interval_seconds": settings.min_interval_seconds,
            "cache_dir": settings.cache_dir,
        }
        values.update(kwargs)
        return cls(**values)

    def fetch(self, capability: str, market: str | None = None) -> FetchResult:
        decision = self.check(capability, market)
        if decision.state is not AnalysisState.AVAILABLE:
            return FetchResult(state=decision.state, reason=decision.reason, capability=capability)
        return FetchResult(
            state=AnalysisState.UNAVAILABLE,
            reason="player_log_query_required",
            capability=capability,
        )

    def fetch_player_logs(
        self,
        nba_player_id: str,
        season: str,
        cutoff: datetime,
        identity: IdentityMap | dict[str, object],
        season_type: str = "Regular Season",
        game_directory: GameDirectory | None = None,
        strict_point_in_time: bool = False,
    ) -> PlayerLogFetch:
        try:
            query = PlayerLogQuery.model_validate(
                {
                    "nba_player_id": nba_player_id,
                    "season": season,
                    "cutoff": cutoff,
                    "identity": identity,
                }
            )
        except ValidationError:
            return _refused("invalid_query")
        if season_type not in SEASON_TYPES:
            return _refused("invalid_query")
        if game_directory is not None and (
            game_directory.season != query.season or game_directory.season_type != season_type
        ):
            return _refused("season_type_mismatch", detail=season_type)
        if query.nba_player_id not in query.identity.players:
            return _refused("unresolved_player_id", native_player_id=query.nba_player_id)

        loaded = self._read_cache(query.nba_player_id, query.season, season_type)
        if loaded is None:
            try:
                rows = self._request_rows(query.nba_player_id, query.season, season_type)
            except _RowRequestError as exc:
                return _refused(exc.reason, exc.detail)
            retrieved_at = require_utc(self._clock())
            self._write_cache(query.nba_player_id, query.season, season_type, retrieved_at, rows)
        else:
            retrieved_at, rows = loaded

        records, quarantined = normalize_playergamelog(
            rows,
            nba_player_id=query.nba_player_id,
            identity=query.identity,
            cutoff=query.cutoff,
            retrieved_at=retrieved_at,
            game_directory=game_directory,
            strict_point_in_time=strict_point_in_time,
        )
        return PlayerLogFetch(
            state=AnalysisState.AVAILABLE,
            retrieved_at=retrieved_at,
            records=records,
            quarantined=quarantined,
        )

    def _cache_path(self, player_id: str, season: str, season_type: str) -> Path:
        slug = "playoffs" if season_type == "Playoffs" else "regular-season"
        return self.cache_dir / ENDPOINT / player_id / season / f"{slug}.json"

    def _read_cache(self, player_id: str, season: str, season_type: str) -> tuple[datetime, list[object]] | None:
        path = self._cache_path(player_id, season, season_type)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != CACHE_VERSION or payload.get("endpoint") != ENDPOINT:
            return None
        if payload.get("nba_player_id") != player_id or payload.get("season") != season:
            return None
        if payload.get("season_type") != season_type:
            return None
        retrieved_at = _parse_cached_time(payload.get("retrieved_at"))
        rows = payload.get("rows")
        if retrieved_at is None or not isinstance(rows, list):
            return None
        return retrieved_at, rows

    def _write_cache(
        self,
        player_id: str,
        season: str,
        season_type: str,
        retrieved_at: datetime,
        rows: list[dict[str, object]],
    ) -> None:
        path = self._cache_path(player_id, season, season_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "endpoint": ENDPOINT,
            "nba_player_id": player_id,
            "season": season,
            "season_type": season_type,
            "retrieved_at": retrieved_at.isoformat(),
            "rows": rows,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request is not None:
            remaining = self.min_interval_seconds - (now - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
                now = self._last_request + self.min_interval_seconds
        self._last_request = now

    def _request_rows(self, player_id: str, season: str, season_type: str) -> list[dict[str, object]]:
        attempts = self.max_retries + 1
        last_name = "provider_error"
        for attempt in range(attempts):
            self._pace()
            try:
                rows = self._row_source(
                    player_id=player_id,
                    season=season,
                    timeout=self.timeout_seconds,
                    season_type=season_type,
                )
            except Exception as exc:
                last_name = type(exc).__name__
                if last_name not in RETRYABLE_ERRORS or attempt == attempts - 1:
                    reason = "provider_timeout" if last_name in RETRYABLE_ERRORS else "provider_error"
                    raise _RowRequestError(reason, last_name) from exc
                continue
            if not isinstance(rows, list):
                raise _RowRequestError("provider_error", "invalid_payload")
            return rows
        raise _RowRequestError("provider_timeout", last_name)


class _RowRequestError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
