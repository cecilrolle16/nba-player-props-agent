"""Validated provider, schedule, and env-example configuration."""

from pathlib import Path
import json
import re
from typing import Literal

import yaml
from pydantic import Field, ValidationError, field_validator

from contracts.capabilities import declaration_status
from contracts.common import AnalysisState, Capability, Market, StrictModel

SECRET_KEYS = (
    "ODDS_API_KEY",
    "THE_ODDS_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
)
REQUIRED_ENV_KEYS = (
    "NBA_RULES_FILE",
    "NBA_PROVIDERS_FILE",
    "NBA_SCHEDULES_FILE",
    "NBA_MODEL_VERSION",
    "NBA_RULES_VERSION",
    *SECRET_KEYS,
)
RUN_AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
JOB_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
IMPLEMENTED_PROVIDER_CAPABILITIES = {
    ("nba-api", frozenset({Capability.PLAYER_LOGS})),
    ("nba-api", frozenset({Capability.PLAYER_LOGS, Capability.SCHEDULES})),
    ("odds-api", frozenset({Capability.MARKETS})),
}


class ConfigStatus(StrictModel):
    state: AnalysisState
    reason: str | None = None
    detail: str | None = None


class ProviderSpec(StrictModel):
    provider_id: str = Field(pattern=PROVIDER_ID.pattern)
    enabled: bool
    capabilities: tuple[Capability, ...]
    supported_markets: tuple[Market, ...] = ()

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: tuple[Capability, ...]) -> tuple[Capability, ...]:
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        return value


class ProviderFile(StrictModel):
    version: int = Field(ge=1)
    providers: tuple[ProviderSpec, ...]


class ScheduleJob(StrictModel):
    job_id: str = Field(pattern=JOB_ID.pattern)
    description: str = Field(min_length=1)
    run_at: str = Field(pattern=RUN_AT.pattern)
    markets: tuple[Market, ...]
    historical_projections_required: bool
    injury_freshness_rule: str = Field(min_length=1)
    adjustments_enabled: bool


class ScheduleFile(StrictModel):
    version: int = Field(ge=1)
    timezone: str
    jobs: tuple[ScheduleJob, ...]

    @field_validator("timezone")
    @classmethod
    def utc_only(cls, value: str) -> str:
        if value != "UTC":
            raise ValueError("unsupported_timezone")
        return value


def _reason_for(exc: ValidationError) -> str:
    text = str(exc)
    for reason in (
        "unknown_provider_capability",
        "unsupported_timezone",
        "unknown_market",
    ):
        if reason in text:
            return reason
    if "enum" in text.lower() or "Input should be" in text:
        blob = text.lower()
        if "capability" in blob or "capabilities" in blob:
            return "unknown_provider_capability"
        if "market" in blob:
            return "unknown_market"
        if "timezone" in blob:
            return "unsupported_timezone"
    return "invalid_configuration"


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_providers(payload: object) -> tuple[ConfigStatus, ProviderFile | None]:
    try:
        parsed = ProviderFile.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason=_reason_for(exc), detail=str(exc)), None
    seen: set[str] = set()
    for provider in parsed.providers:
        if provider.provider_id in seen:
            return (
                ConfigStatus(
                    state=AnalysisState.UNAVAILABLE,
                    reason="duplicate_provider_id",
                    detail=provider.provider_id,
                ),
                None,
            )
        seen.add(provider.provider_id)
        declared = tuple(item.value for item in provider.capabilities)
        status = declaration_status(declared)
        if status.state is not AnalysisState.AVAILABLE:
            return ConfigStatus(state=status.state, reason=status.reason, detail=provider.provider_id), None
        market_caps = {Capability.MARKETS, Capability.PROJECTIONS}
        if market_caps.intersection(provider.capabilities) and not provider.supported_markets:
            return (
                ConfigStatus(
                    state=AnalysisState.UNAVAILABLE,
                    reason="unsupported_market_scope",
                    detail=provider.provider_id,
                ),
                None,
            )
        if provider.enabled:
            implemented = (provider.provider_id, frozenset(provider.capabilities))
            if implemented not in IMPLEMENTED_PROVIDER_CAPABILITIES:
                return (
                    ConfigStatus(
                        state=AnalysisState.UNAVAILABLE,
                        reason="live_provider_not_implemented",
                        detail=provider.provider_id,
                    ),
                    None,
                )
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


class TeamAbbreviation(StrictModel):
    abbreviation: str = Field(pattern=r"^[A-Z]{2,3}$")
    nba_team_id: str = Field(pattern=r"^\d{10}$")


class TeamAbbreviationFile(StrictModel):
    version: Literal[1]
    source: str = Field(min_length=1)
    teams: tuple[TeamAbbreviation, ...]

    @field_validator("teams")
    @classmethod
    def unique_teams(cls, value: tuple[TeamAbbreviation, ...]) -> tuple[TeamAbbreviation, ...]:
        abbreviations = [item.abbreviation for item in value]
        team_ids = [item.nba_team_id for item in value]
        if len(abbreviations) != len(set(abbreviations)) or len(team_ids) != len(set(team_ids)):
            raise ValueError("duplicate team identity")
        if len(value) < 1:
            raise ValueError("team map is empty")
        return value


class NbaApiScheduleSettings(StrictModel):
    """Operational limits for the schedule identity adapter. This file does not enable a fetch."""

    version: Literal[1]
    provider_id: Literal["nba-api"]
    endpoint: Literal["scheduleleaguev2"]
    timeout_seconds: float = Field(gt=0, le=30)
    max_retries: int = Field(ge=0, le=2)
    min_interval_seconds: float = Field(ge=0.6, le=30)
    cache_dir: str = Field(min_length=1)
    team_map: str = Field(min_length=1)


class NbaApiLogSettings(StrictModel):
    """Operational limits for the playergamelog adapter. This file does not enable a fetch."""

    version: Literal[1]
    provider_id: Literal["nba-api"]
    endpoint: Literal["playergamelog"]
    timeout_seconds: float = Field(gt=0, le=30)
    max_retries: int = Field(ge=0, le=2)
    min_interval_seconds: float = Field(ge=0.6, le=30)
    cache_dir: str = Field(min_length=1)
    season_type: Literal["Regular Season"]


class OddsApiTeamName(StrictModel):
    name: str = Field(min_length=1)
    nba_team_id: str = Field(pattern=r"^\d{10}$")


class OddsApiTeamNameFile(StrictModel):
    version: Literal[1]
    source: str = Field(min_length=1)
    teams: tuple[OddsApiTeamName, ...]

    @field_validator("teams")
    @classmethod
    def unique_teams(cls, value: tuple[OddsApiTeamName, ...]) -> tuple[OddsApiTeamName, ...]:
        names = [item.name.casefold() for item in value]
        team_ids = [item.nba_team_id for item in value]
        if len(names) != len(set(names)) or len(team_ids) != len(set(team_ids)):
            raise ValueError("duplicate team identity")
        if len(value) < 1:
            raise ValueError("team name map is empty")
        return value


class OddsApiSettings(StrictModel):
    """Operational limits for one event-odds request. This file does not enable a fetch.

    max_quote_age_seconds is an unvalidated operational default. freshness_limit_validated
    stays false until a measured threshold replaces it.
    """

    version: Literal[1]
    provider_id: Literal["odds-api"]
    endpoint: Literal["event-odds"]
    sport: Literal["basketball_nba"]
    region: str = Field(pattern=r"^[a-z0-9]{2,12}$")
    markets: tuple[Literal["player_points", "player_assists", "player_rebounds"], ...]
    bookmakers: tuple[str, ...] = ()
    timeout_seconds: float = Field(gt=0, le=30)
    max_retries: int = Field(ge=0, le=2)
    min_interval_seconds: float = Field(ge=0.6, le=30)
    cache_dir: str = Field(min_length=1)
    api_key_env: Literal["THE_ODDS_API_KEY"]
    team_names: str = Field(min_length=1)
    max_quote_age_seconds: int = Field(gt=0, le=3600)
    freshness_limit_validated: Literal[False]
    max_request_credits: int = Field(ge=1, le=3)

    @field_validator("markets")
    @classmethod
    def required_markets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        expected = ("player_points", "player_assists", "player_rebounds")
        if value != expected:
            raise ValueError("markets must be player_points, player_assists, and player_rebounds")
        return value

    @field_validator("bookmakers")
    @classmethod
    def bookmaker_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 10:
            raise ValueError("bookmakers must stay within one region unit")
        if len(value) != len(set(value)):
            raise ValueError("bookmakers must be unique")
        for item in value:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,40}", item):
                raise ValueError("bookmaker key is not a provider key")
        return value


def validate_odds_api_settings(payload: object) -> tuple[ConfigStatus, OddsApiSettings | None]:
    try:
        parsed = OddsApiSettings.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


def load_odds_api_settings(path: Path) -> tuple[ConfigStatus, OddsApiSettings | None]:
    try:
        payload = load_yaml(path)
    except OSError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return validate_odds_api_settings(payload)


def validate_odds_api_teams(payload: object) -> tuple[ConfigStatus, OddsApiTeamNameFile | None]:
    try:
        parsed = OddsApiTeamNameFile.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


def load_odds_api_teams(path: Path) -> tuple[ConfigStatus, OddsApiTeamNameFile | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return validate_odds_api_teams(payload)


def validate_nba_api_log_settings(payload: object) -> tuple[ConfigStatus, NbaApiLogSettings | None]:
    try:
        parsed = NbaApiLogSettings.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


def load_nba_api_log_settings(path: Path) -> tuple[ConfigStatus, NbaApiLogSettings | None]:
    try:
        payload = load_yaml(path)
    except OSError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return validate_nba_api_log_settings(payload)


def validate_nba_api_schedule_settings(payload: object) -> tuple[ConfigStatus, NbaApiScheduleSettings | None]:
    try:
        parsed = NbaApiScheduleSettings.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


def load_nba_api_schedule_settings(path: Path) -> tuple[ConfigStatus, NbaApiScheduleSettings | None]:
    try:
        payload = load_yaml(path)
    except OSError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return validate_nba_api_schedule_settings(payload)


def validate_team_abbreviations(payload: object) -> tuple[ConfigStatus, TeamAbbreviationFile | None]:
    try:
        parsed = TeamAbbreviationFile.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


def load_team_abbreviations(path: Path) -> tuple[ConfigStatus, TeamAbbreviationFile | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(exc)), None
    return validate_team_abbreviations(payload)


def validate_schedules(payload: object) -> tuple[ConfigStatus, ScheduleFile | None]:
    try:
        parsed = ScheduleFile.model_validate(payload)
    except ValidationError as exc:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason=_reason_for(exc), detail=str(exc)), None
    if parsed.timezone != "UTC":
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="unsupported_timezone"), None
    seen: set[str] = set()
    for job in parsed.jobs:
        if job.job_id in seen:
            return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="duplicate_job_id", detail=job.job_id), None
        seen.add(job.job_id)
        if job.adjustments_enabled:
            return (
                ConfigStatus(
                    state=AnalysisState.UNAVAILABLE,
                    reason="adjustments_not_permitted",
                    detail=job.job_id,
                ),
                None,
            )
        if set(job.markets) != set(Market):
            return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="unsupported_market_scope", detail=job.job_id), None
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), parsed


def parse_env_example(text: str) -> tuple[ConfigStatus, dict[str, str]]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line == "" or line.startswith("#"):
            continue
        if "=" not in line:
            return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration", detail=str(line_number)), {}
        key, value = line.split("=", 1)
        if key in values:
            return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="duplicate_env_key", detail=key), {}
        values[key] = value
    missing = [key for key in REQUIRED_ENV_KEYS if key not in values]
    if missing:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="missing_env_key", detail=",".join(missing)), values
    filled = [key for key in SECRET_KEYS if values.get(key, "") != ""]
    if filled:
        return ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="secret_present", detail=",".join(filled)), values
    return ConfigStatus(state=AnalysisState.AVAILABLE, reason=None), values


def load_provider_file(path: Path) -> tuple[ConfigStatus, ProviderFile | None]:
    return validate_providers(load_yaml(path))


def load_schedule_file(path: Path) -> tuple[ConfigStatus, ScheduleFile | None]:
    return validate_schedules(load_yaml(path))


def load_env_example(path: Path) -> tuple[ConfigStatus, dict[str, str]]:
    return parse_env_example(path.read_text(encoding="utf-8"))
