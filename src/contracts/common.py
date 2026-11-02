"""Shared types for canonical records."""

import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def require_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC, not a fixed offset")
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(require_utc)]


class Market(str, Enum):
    POINTS = "points"
    ASSISTS = "assists"
    REBOUNDS = "rebounds"


class Side(str, Enum):
    OVER = "over"
    UNDER = "under"


class InjuryStatus(str, Enum):
    OUT = "out"
    DOUBTFUL = "doubtful"
    QUESTIONABLE = "questionable"
    PROBABLE = "probable"
    AVAILABLE = "available"


class GameStatus(str, Enum):
    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    FINAL = "final"


class AnalysisState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    SKIP = "skip"


class Capability(str, Enum):
    SCHEDULES = "schedules"
    ROSTERS = "rosters"
    PLAYER_LOGS = "player_logs"
    TEAM_CONTEXT = "team_context"
    INJURIES = "injuries"
    MARKETS = "markets"
    PROJECTIONS = "projections"


KNOWN_CAPABILITIES = frozenset(item.value for item in Capability)
MARKET_CAPABILITIES = frozenset({Capability.MARKETS.value, Capability.PROJECTIONS.value})
BLOCKING_EFFECTS = frozenset({AnalysisState.UNAVAILABLE.value, AnalysisState.SKIP.value})

PLAYER_ID = re.compile(r"^player:[a-z0-9][a-z0-9-]{0,80}$")
TEAM_ID = re.compile(r"^team:[a-z0-9][a-z0-9-]{0,80}$")
GAME_ID = re.compile(r"^game:[a-z0-9][a-z0-9-]{0,80}$")
SNAPSHOT_ID = re.compile(r"^snapshot:[a-z0-9][a-z0-9-]{0,80}$")
PROJECTION_ID = re.compile(r"^projection:[a-z0-9][a-z0-9-]{0,80}$")
FEATURE_ID = re.compile(r"^feature:[a-z0-9][a-z0-9-]{0,80}$")
COMPARISON_ID = re.compile(r"^comparison:[a-z0-9][a-z0-9-]{0,80}$")
DECISION_ID = re.compile(r"^decision:[a-z0-9][a-z0-9-]{0,80}$")
SETTLEMENT_ID = re.compile(r"^settlement:[a-z0-9][a-z0-9-]{0,80}$")
REPORT_ID = re.compile(r"^report:[a-z0-9][a-z0-9-]{0,80}$")
PRICE_ID = re.compile(r"^price:[a-z0-9][a-z0-9-]{0,80}$")
AMERICAN_ODDS = re.compile(r"^[+-](?:100|[1-9]\d{2,5})$")


def _id_validator(pattern: re.Pattern[str], label: str):
    def validate(value: str) -> str:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ValueError(f"{label} is not a canonical id")
        return value

    return validate


PlayerId = Annotated[str, AfterValidator(_id_validator(PLAYER_ID, "player"))]
TeamId = Annotated[str, AfterValidator(_id_validator(TEAM_ID, "team"))]
GameId = Annotated[str, AfterValidator(_id_validator(GAME_ID, "game"))]
SnapshotId = Annotated[str, AfterValidator(_id_validator(SNAPSHOT_ID, "snapshot"))]
ProjectionId = Annotated[str, AfterValidator(_id_validator(PROJECTION_ID, "projection"))]
FeatureId = Annotated[str, AfterValidator(_id_validator(FEATURE_ID, "feature"))]
ComparisonId = Annotated[str, AfterValidator(_id_validator(COMPARISON_ID, "comparison"))]
DecisionId = Annotated[str, AfterValidator(_id_validator(DECISION_ID, "decision"))]
SettlementId = Annotated[str, AfterValidator(_id_validator(SETTLEMENT_ID, "settlement"))]
ReportId = Annotated[str, AfterValidator(_id_validator(REPORT_ID, "report"))]
PriceId = Annotated[str, AfterValidator(_id_validator(PRICE_ID, "price"))]


class SourceId(StrictModel):
    provider_id: str
    native_id: str

    @field_validator("provider_id", "native_id")
    @classmethod
    def clean(cls, value: str) -> str:
        if not isinstance(value, str) or value.strip() == "" or value != value.strip():
            raise ValueError("source id fields must be non-empty stripped strings")
        return value


def require_sources(value: tuple[SourceId, ...]) -> tuple[SourceId, ...]:
    if len(value) < 1:
        raise ValueError("source_ids must contain at least one source id")
    return value
