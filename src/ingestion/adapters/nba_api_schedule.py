"""Map scheduleleaguev2 rows into canonical game and team identities.

Game and team ids come from the source row. The abbreviation map only checks
that a tricode and a team id name the same maintained team. Regular-season
game ids start with 002. Playoff game ids start with 004. Other prefixes stay
out of both directories.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Literal, Protocol

from pydantic import Field, ValidationError, field_validator

from contracts.common import UTCDateTime, AnalysisState, GameId, GameStatus, SourceId, StrictModel, TeamId, require_utc
from contracts.configuration import NbaApiScheduleSettings, TeamAbbreviationFile
from contracts.schedule import ScheduleGame
from ingestion.adapters.base import FetchResult, ProviderAdapter

PROVIDER_ID = "nba-api"
ENDPOINT = "scheduleleaguev2"
SEASON_TYPES = ("Regular Season", "Playoffs")
SeasonTypeName = Literal["Regular Season", "Playoffs"]
SEASON_PREFIX = {"Regular Season": "002", "Playoffs": "004"}
CACHE_VERSION = 1
POINT_IN_TIME_NOTE = (
    "scheduleleaguev2 has no as-of parameter. A current extract cannot establish historical availability. "
    "available_at is the retrieval time. tip_at is the source UTC tip, not evidence that this payload was published then."
)
GAME_NATIVE = re.compile(r"^\d{10}$")
TEAM_NATIVE = re.compile(r"^\d{10}$")
MATCHUP_SIDES = re.compile(r"^([A-Za-z]{2,3})\s+(vs\.?|@)\s+([A-Za-z]{2,3})$")
RETRYABLE_ERRORS = frozenset(
    {"Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutError", "ConnectionError"}
)


class TeamAbbreviationMap(StrictModel):
    by_abbreviation: dict[str, str]
    by_team_id: dict[str, str]

    @classmethod
    def from_file(cls, parsed: TeamAbbreviationFile) -> "TeamAbbreviationMap":
        by_abbreviation = {item.abbreviation: item.nba_team_id for item in parsed.teams}
        by_team_id = {item.nba_team_id: item.abbreviation for item in parsed.teams}
        return cls(by_abbreviation=by_abbreviation, by_team_id=by_team_id)

    def canonical(self, nba_team_id: str) -> str:
        return f"team:{nba_team_id}"

    def canonical_abbreviations(self) -> dict[str, str]:
        return {abbreviation: self.canonical(team_id) for abbreviation, team_id in self.by_abbreviation.items()}


class ResolvedGame(StrictModel):
    native_game_id: str = Field(pattern=GAME_NATIVE.pattern)
    canonical_game_id: GameId
    season_type: SeasonTypeName
    home_team_id: TeamId
    away_team_id: TeamId
    home_abbreviation: str
    away_abbreviation: str
    tip_at: UTCDateTime
    record: ScheduleGame


class GameDirectory(StrictModel):
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    season_type: SeasonTypeName
    retrieved_at: UTCDateTime
    abbreviations: dict[str, TeamId]
    games: dict[str, ResolvedGame]
    blocked: dict[str, str]


class QuarantinedGame(StrictModel):
    reason: str
    native_game_id: str | None = None
    detail: str | None = None


class ScheduleFetch(StrictModel):
    state: AnalysisState
    reason: str | None = None
    detail: str | None = None
    retrieved_at: UTCDateTime | None = None
    point_in_time_limited: bool = True
    point_in_time_note: str = POINT_IN_TIME_NOTE
    season: str | None = None
    season_type: str | None = None
    directory: GameDirectory | None = None
    records: tuple[ScheduleGame, ...] = ()
    quarantined: tuple[QuarantinedGame, ...] = ()

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
            raise ValueError("scheduleleaguev2 results are point-in-time limited")
        return value


class ScheduleRowSource(Protocol):
    def __call__(self, *, season: str, timeout: float) -> list[dict[str, object]]:
        """Return raw SeasonGames rows. Implementations may perform network I/O."""


def canonical_game_id(native_game_id: str) -> str:
    return f"game:{native_game_id}"


def parse_matchup(value: object) -> tuple[str, str, bool] | None:
    """Return player abbreviation, opponent abbreviation, and whether the player is home."""

    if not isinstance(value, str):
        return None
    matched = MATCHUP_SIDES.fullmatch(value.strip())
    if matched is None:
        return None
    player, marker, opponent = matched.group(1), matched.group(2), matched.group(3)
    return player.upper(), opponent.upper(), marker.startswith("vs")


def _native_game_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str) and value.strip().isdigit():
        text = value.strip()
    else:
        return None
    if len(text) == 8:
        text = text.zfill(10)
    if GAME_NATIVE.fullmatch(text) is None:
        return None
    return text


def _native_team_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str) and value.strip().isdigit():
        text = value.strip()
    else:
        return None
    if TEAM_NATIVE.fullmatch(text) is None:
        return None
    return text


def _parse_tip(value: object) -> datetime | None:
    if not isinstance(value, str) or value.strip() == "":
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    try:
        return require_utc(parsed)
    except (TypeError, ValueError):
        return None


def _truthy_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false"}
    return False


def _game_status(row: dict[str, object]) -> GameStatus | None:
    if _truthy_flag(row.get("postponedStatus")):
        return GameStatus.POSTPONED
    status = row.get("gameStatus")
    text = row.get("gameStatusText")
    label = text.strip().lower() if isinstance(text, str) else ""
    if status in {3, "3"} or label.startswith("final"):
        return GameStatus.FINAL
    if status in {1, "1"} or label in {"scheduled", "pregame"}:
        return GameStatus.SCHEDULED
    return None


def _season_year_matches(season: str, value: object) -> bool:
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return False
    return text == season or text == season[:4]


def _quarantine(reason: str, native_game_id: str | None, detail: str | None = None) -> QuarantinedGame:
    return QuarantinedGame(reason=reason, native_game_id=native_game_id, detail=detail)


def _candidate_signature(game: ResolvedGame) -> tuple[str, str, str, str]:
    return (game.home_team_id, game.away_team_id, game.tip_at.isoformat(), game.record.status.value)


def _parse_row(
    row: dict[str, object],
    *,
    season: str,
    season_type: SeasonTypeName,
    team_map: TeamAbbreviationMap,
    retrieved_at: datetime,
) -> ResolvedGame | QuarantinedGame:
    native_game_id = _native_game_id(row.get("gameId"))
    if native_game_id is None:
        return _quarantine("invalid_game_id", None, "gameId")
    prefix = SEASON_PREFIX[season_type]
    if not native_game_id.startswith(prefix):
        return _quarantine("season_type_mismatch", native_game_id, native_game_id[:3])
    if not _season_year_matches(season, row.get("seasonYear")):
        return _quarantine("season_mismatch", native_game_id, "seasonYear")
    if _truthy_flag(row.get("isNeutral")):
        return _quarantine("neutral_site", native_game_id)
    tip_at = _parse_tip(row.get("gameDateTimeUTC"))
    if tip_at is None:
        return _quarantine("missing_tip_time", native_game_id, "gameDateTimeUTC")
    status = _game_status(row)
    if status is None:
        return _quarantine("unsupported_game_status", native_game_id, "gameStatus")
    resolved_teams: list[tuple[str, str]] = []
    for side in ("home", "away"):
        abbreviation = row.get(f"{side}Team_teamTricode")
        native_team_id = _native_team_id(row.get(f"{side}Team_teamId"))
        if not isinstance(abbreviation, str) or abbreviation.strip() == "":
            return _quarantine("unknown_abbreviation", native_game_id, side)
        abbreviation = abbreviation.strip().upper()
        mapped_team_id = team_map.by_abbreviation.get(abbreviation)
        if mapped_team_id is None:
            return _quarantine("unknown_abbreviation", native_game_id, abbreviation)
        if native_team_id is None:
            return _quarantine("unknown_team_id", native_game_id, side)
        if mapped_team_id != native_team_id or team_map.by_team_id.get(native_team_id) != abbreviation:
            return _quarantine("team_conflict", native_game_id, f"{abbreviation}:{native_team_id}")
        resolved_teams.append((abbreviation, team_map.canonical(native_team_id)))
    home_abbreviation, home_team_id = resolved_teams[0]
    away_abbreviation, away_team_id = resolved_teams[1]
    if home_team_id == away_team_id:
        return _quarantine("team_conflict", native_game_id, "same_team")
    record = ScheduleGame(
        canonical_game_id=canonical_game_id(native_game_id),
        snapshot_id=f"snapshot:nba-sched-{native_game_id}",
        season=season,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        status=status,
        tip_at=tip_at,
        available_at=retrieved_at,
        source_ids=(SourceId(provider_id=PROVIDER_ID, native_id=f"{ENDPOINT}:{native_game_id}"),),
    )
    return ResolvedGame(
        native_game_id=native_game_id,
        canonical_game_id=record.canonical_game_id,
        season_type=season_type,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_abbreviation=home_abbreviation,
        away_abbreviation=away_abbreviation,
        tip_at=tip_at,
        record=record,
    )


def build_game_directory(
    rows: list[object],
    *,
    season: str,
    season_type: SeasonTypeName,
    team_map: TeamAbbreviationMap,
    retrieved_at: datetime,
) -> tuple[GameDirectory, tuple[QuarantinedGame, ...]]:
    retrieved_at = require_utc(retrieved_at)
    quarantined: list[QuarantinedGame] = []
    grouped: dict[str, list[ResolvedGame]] = defaultdict(list)
    blocked: dict[str, str] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            quarantined.append(_quarantine("invalid_row", None))
            continue
        row = {str(key): value for key, value in raw.items()}
        parsed = _parse_row(
            row,
            season=season,
            season_type=season_type,
            team_map=team_map,
            retrieved_at=retrieved_at,
        )
        if isinstance(parsed, QuarantinedGame):
            quarantined.append(parsed)
            if parsed.native_game_id is not None and parsed.reason != "duplicate_mapping":
                blocked.setdefault(parsed.native_game_id, parsed.reason)
            continue
        grouped[parsed.native_game_id].append(parsed)

    accepted: dict[str, ResolvedGame] = {}
    for native_game_id, candidates in grouped.items():
        signatures = {_candidate_signature(candidate) for candidate in candidates}
        if native_game_id in blocked or len(signatures) > 1:
            blocked[native_game_id] = "conflicting_game_identity" if len(signatures) > 1 else blocked[native_game_id]
            for candidate in candidates:
                quarantined.append(_quarantine(blocked[native_game_id], native_game_id, candidate.canonical_game_id))
            continue
        accepted[native_game_id] = candidates[0]
        for extra in candidates[1:]:
            quarantined.append(_quarantine("duplicate_mapping", native_game_id, extra.canonical_game_id))
        blocked.pop(native_game_id, None)

    directory = GameDirectory(
        season=season,
        season_type=season_type,
        retrieved_at=retrieved_at,
        abbreviations=team_map.canonical_abbreviations(),
        games=accepted,
        blocked={key: value for key, value in blocked.items() if key not in accepted},
    )
    return directory, tuple(quarantined)


def _live_rows(*, season: str, timeout: float) -> list[dict[str, object]]:
    from ingestion.adapters.nba_api_client import fetch_schedule_rows

    return fetch_schedule_rows(season=season, timeout=timeout)


def _parse_cached_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return require_utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


class _RowRequestError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail


class NbaApiScheduleAdapter(ProviderAdapter):
    """Fetch one season schedule and split regular season from playoffs."""

    def __init__(
        self,
        team_map: TeamAbbreviationMap,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        min_interval_seconds: float = 1.0,
        cache_dir: Path | str = Path("data/cache/nba-api"),
        row_source: ScheduleRowSource | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(PROVIDER_ID, ("schedules",))
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be from 0 through 2")
        if not 0 <= min_interval_seconds <= 30:
            raise ValueError("min_interval_seconds must be from 0 through 30")
        self.team_map = team_map
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
    def from_settings(
        cls,
        settings: NbaApiScheduleSettings,
        team_map: TeamAbbreviationMap,
        **kwargs: object,
    ) -> "NbaApiScheduleAdapter":
        values: dict[str, object] = {
            "timeout_seconds": settings.timeout_seconds,
            "max_retries": settings.max_retries,
            "min_interval_seconds": settings.min_interval_seconds,
            "cache_dir": settings.cache_dir,
        }
        values.update(kwargs)
        return cls(team_map, **values)

    def fetch(self, capability: str, market: str | None = None) -> FetchResult:
        decision = self.check(capability, market)
        if decision.state is not AnalysisState.AVAILABLE:
            return FetchResult(state=decision.state, reason=decision.reason, capability=capability)
        return FetchResult(
            state=AnalysisState.UNAVAILABLE,
            reason="schedule_query_required",
            capability=capability,
        )

    def fetch_schedule(self, season: str, season_type: str) -> ScheduleFetch:
        if season_type not in SEASON_TYPES or re.fullmatch(r"\d{4}-\d{2}", season) is None:
            return ScheduleFetch(state=AnalysisState.UNAVAILABLE, reason="invalid_query")
        loaded = self._read_cache(season)
        if loaded is None:
            try:
                rows = self._request_rows(season)
            except _RowRequestError as exc:
                return ScheduleFetch(state=AnalysisState.UNAVAILABLE, reason=exc.reason, detail=exc.detail)
            retrieved_at = require_utc(self._clock())
            self._write_cache(season, retrieved_at, rows)
        else:
            retrieved_at, rows = loaded
        directory, quarantined = build_game_directory(
            rows,
            season=season,
            season_type=season_type,
            team_map=self.team_map,
            retrieved_at=retrieved_at,
        )
        records = tuple(game.record for game in sorted(directory.games.values(), key=lambda item: item.native_game_id))
        return ScheduleFetch(
            state=AnalysisState.AVAILABLE,
            retrieved_at=retrieved_at,
            season=season,
            season_type=season_type,
            directory=directory,
            records=records,
            quarantined=quarantined,
        )

    def _cache_path(self, season: str) -> Path:
        return self.cache_dir / ENDPOINT / f"{season}.json"

    def _read_cache(self, season: str) -> tuple[datetime, list[object]] | None:
        path = self._cache_path(season)
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
        if payload.get("season") != season:
            return None
        retrieved_at = _parse_cached_time(payload.get("retrieved_at"))
        rows = payload.get("rows")
        if retrieved_at is None or not isinstance(rows, list):
            return None
        return retrieved_at, rows

    def _write_cache(self, season: str, retrieved_at: datetime, rows: list[object]) -> None:
        path = self._cache_path(season)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "endpoint": ENDPOINT,
            "season": season,
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

    def _request_rows(self, season: str) -> list[object]:
        attempts = self.max_retries + 1
        last_name = "provider_error"
        for attempt in range(attempts):
            self._pace()
            try:
                rows = self._row_source(season=season, timeout=self.timeout_seconds)
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
