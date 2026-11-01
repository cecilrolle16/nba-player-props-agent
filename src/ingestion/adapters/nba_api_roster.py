"""Map one CommonTeamRoster response into RosterEntry records.

Player and team ids come from the source row. Names are stored as returned.
commonteamroster has no as-of parameter, so available_at is the retrieval time.
"""

from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Protocol

from pydantic import ValidationError, field_validator

from contracts.common import UTCDateTime, AnalysisState, SourceId, StrictModel, require_utc
from contracts.configuration import NbaApiRosterSettings
from contracts.roster import RosterEntry
from ingestion.adapters.base import FetchResult, ProviderAdapter
from ingestion.adapters.nba_api_client import fetch_commonteamroster_rows
from ingestion.adapters.nba_api_schedule import TeamAbbreviationMap

PROVIDER_ID = "nba-api"
ENDPOINT = "commonteamroster"
CACHE_VERSION = 1
POINT_IN_TIME_NOTE = (
    "commonteamroster has no as-of parameter. A current extract cannot establish historical availability. "
    "available_at is the retrieval time."
)
TEAM_NATIVE = re.compile(r"^\d{10}$")
PLAYER_NATIVE = re.compile(r"^[1-9]\d{0,9}$")
SEASON = re.compile(r"^\d{4}-\d{2}$")
RETRYABLE_ERRORS = frozenset(
    {"Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutError", "ConnectionError"}
)
ACCEPTED_SUPPLEMENTAL = {0, 1, "0", "1"}


class QuarantinedPlayer(StrictModel):
    reason: str
    native_player_id: str | None = None
    detail: str | None = None


class RosterFetch(StrictModel):
    state: AnalysisState
    reason: str | None = None
    detail: str | None = None
    retrieved_at: UTCDateTime | None = None
    point_in_time_limited: bool = True
    point_in_time_note: str = POINT_IN_TIME_NOTE
    season: str | None = None
    team_id: str | None = None
    records: tuple[RosterEntry, ...] = ()
    quarantined: tuple[QuarantinedPlayer, ...] = ()

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
            raise ValueError("commonteamroster results are point-in-time limited")
        return value


class RosterRowSource(Protocol):
    def __call__(self, *, team_id: str, season: str, timeout: float) -> list[dict[str, object]]:
        """Return raw CommonTeamRoster rows. Implementations may perform network I/O."""


class _RowRequestError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail


def _native_player_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str) and value.strip().isdigit():
        text = value.strip()
    else:
        return None
    if PLAYER_NATIVE.fullmatch(text) is None:
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


def _season_year_matches(season: str, value: object) -> bool:
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return False
    return text == season or text == season[:4]


def _league_is_nba(value: object) -> bool:
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.strip() in {"0", "00"}
    return False


def _parse_cached_time(value: object) -> datetime | None:
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


def _quarantine(reason: str, native_player_id: str | None, detail: str | None = None) -> QuarantinedPlayer:
    return QuarantinedPlayer(reason=reason, native_player_id=native_player_id, detail=detail)


def build_roster(
    rows: list[object],
    *,
    season: str,
    team_id: str,
    retrieved_at: datetime,
) -> tuple[tuple[RosterEntry, ...], tuple[QuarantinedPlayer, ...]]:
    """Accept roster rows whose source ids match the requested team and season."""

    parsed: list[RosterEntry] = []
    quarantined: list[QuarantinedPlayer] = []
    for row in rows:
        if not isinstance(row, dict):
            quarantined.append(_quarantine("malformed_roster_row", None))
            continue
        native_player_id = _native_player_id(row.get("PLAYER_ID"))
        if native_player_id is None:
            quarantined.append(_quarantine("invalid_player_id", None, "PLAYER_ID"))
            continue
        native_team_id = _native_team_id(row.get("TeamID"))
        if native_team_id is None:
            quarantined.append(_quarantine("invalid_team_id", native_player_id, "TeamID"))
            continue
        if native_team_id != team_id:
            quarantined.append(_quarantine("team_mismatch", native_player_id, native_team_id))
            continue
        if not _season_year_matches(season, row.get("SEASON")):
            quarantined.append(_quarantine("season_mismatch", native_player_id, "SEASON"))
            continue
        if not _league_is_nba(row.get("LeagueID")):
            quarantined.append(_quarantine("league_mismatch", native_player_id, "LeagueID"))
            continue
        name = row.get("PLAYER")
        if not isinstance(name, str) or name.strip() == "":
            quarantined.append(_quarantine("missing_name", native_player_id))
            continue
        if row.get("SUPPLEMENTAL_STATUS") not in ACCEPTED_SUPPLEMENTAL:
            quarantined.append(_quarantine("unknown_roster_status", native_player_id, "SUPPLEMENTAL_STATUS"))
            continue
        try:
            record = RosterEntry(
                canonical_player_id=f"player:{native_player_id}",
                canonical_team_id=f"team:{native_team_id}",
                season=season,
                display_name=name.strip(),
                active=True,
                available_at=retrieved_at,
                source_ids=(
                    SourceId(
                        provider_id=PROVIDER_ID,
                        native_id=f"{ENDPOINT}:{season}:{native_team_id}:{native_player_id}",
                    ),
                ),
            )
        except ValidationError:
            quarantined.append(_quarantine("malformed_roster_row", native_player_id))
            continue
        parsed.append(record)

    counts: dict[str, int] = {}
    for record in parsed:
        counts[record.canonical_player_id] = counts.get(record.canonical_player_id, 0) + 1
    records: list[RosterEntry] = []
    for record in parsed:
        if counts[record.canonical_player_id] > 1:
            quarantined.append(
                _quarantine("duplicate_player", record.canonical_player_id.removeprefix("player:"), record.display_name)
            )
            continue
        records.append(record)
    records.sort(key=lambda item: item.canonical_player_id)
    return tuple(records), tuple(quarantined)


class NbaApiRosterAdapter(ProviderAdapter):
    """Fetch one team's roster for one season."""

    def __init__(
        self,
        team_map: TeamAbbreviationMap,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        min_interval_seconds: float = 1.0,
        cache_dir: Path | str = Path("data/cache/nba-api"),
        row_source: RosterRowSource | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(PROVIDER_ID, ("rosters",))
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
        self._row_source = row_source or fetch_commonteamroster_rows
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleeper or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._last_request: float | None = None

    @classmethod
    def from_settings(
        cls,
        settings: NbaApiRosterSettings,
        team_map: TeamAbbreviationMap,
        **kwargs: object,
    ) -> "NbaApiRosterAdapter":
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
            reason="roster_query_required",
            capability=capability,
        )

    def fetch_roster(self, team_id: str, season: str) -> RosterFetch:
        if SEASON.fullmatch(season) is None:
            return RosterFetch(state=AnalysisState.UNAVAILABLE, reason="invalid_query")
        if TEAM_NATIVE.fullmatch(team_id) is None or team_id not in self.team_map.by_team_id:
            return RosterFetch(state=AnalysisState.UNAVAILABLE, reason="unknown_team", detail=team_id)
        loaded = self._read_cache(season, team_id)
        if loaded is None:
            try:
                rows = self._request_rows(team_id, season)
            except _RowRequestError as exc:
                return RosterFetch(state=AnalysisState.UNAVAILABLE, reason=exc.reason, detail=exc.detail)
            retrieved_at = require_utc(self._clock())
            self._write_cache(season, team_id, retrieved_at, rows)
        else:
            retrieved_at, rows = loaded
        records, quarantined = build_roster(rows, season=season, team_id=team_id, retrieved_at=retrieved_at)
        return RosterFetch(
            state=AnalysisState.AVAILABLE,
            retrieved_at=retrieved_at,
            season=season,
            team_id=team_id,
            records=records,
            quarantined=quarantined,
        )

    def _cache_path(self, season: str, team_id: str) -> Path:
        return self.cache_dir / ENDPOINT / season / f"{team_id}.json"

    def _read_cache(self, season: str, team_id: str) -> tuple[datetime, list[object]] | None:
        path = self._cache_path(season, team_id)
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
        if payload.get("season") != season or payload.get("team_id") != team_id:
            return None
        retrieved_at = _parse_cached_time(payload.get("retrieved_at"))
        rows = payload.get("rows")
        if retrieved_at is None or not isinstance(rows, list):
            return None
        return retrieved_at, rows

    def _write_cache(self, season: str, team_id: str, retrieved_at: datetime, rows: list[object]) -> None:
        path = self._cache_path(season, team_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "endpoint": ENDPOINT,
            "season": season,
            "team_id": team_id,
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

    def _request_rows(self, team_id: str, season: str) -> list[object]:
        attempts = self.max_retries + 1
        last_name = "provider_error"
        for attempt in range(attempts):
            self._pace()
            try:
                rows = self._row_source(team_id=team_id, season=season, timeout=self.timeout_seconds)
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
