"""Evidence used by custom angle policies. Missing facts stay null."""

from enum import Enum

from pydantic import Field

from contracts.common import UTCDateTime, GameId, ProjectionId, SnapshotId, StrictModel


class EvidenceMark(str, Enum):
    INDEPENDENT = "independent"
    CORRELATED = "correlated"
    ABSENT = "absent"


class CauseState(str, Enum):
    EXCLUDED = "excluded"
    PRESENT = "present"
    UNCERTAIN = "uncertain"
    MISSING = "missing"


class AvailabilityEvidence(StrictModel):
    snapshot_id: SnapshotId | None = None
    status_known: bool = False
    injury_news: bool = False
    available_at: UTCDateTime | None = None


class MinutesEvidence(StrictModel):
    snapshot_id: SnapshotId | None = None
    known: bool = False
    expected_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    changed: bool = False
    restricted: bool = False
    available_at: UTCDateTime | None = None


class RoleEvidence(StrictModel):
    snapshot_id: SnapshotId | None = None
    changed: bool = False
    available_at: UTCDateTime | None = None


class DefenderReturnEvidence(StrictModel):
    return_date_snapshot_id: SnapshotId | None = None
    confirmed_return_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    games_since_return: int | None = Field(default=None, ge=0)
    minutes_since_return: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    minutes_snapshot_id: SnapshotId | None = None
    key_defender_snapshot_id: SnapshotId | None = None
    opponent_is_key_defender: bool | None = None
    minutes_restriction: str | None = None
    defensive_rotation_snapshot_id: SnapshotId | None = None
    defensive_rotation_support: str | None = None
    normal_minutes_supported: bool | None = None
    healthy_defensive_performance_supported: bool | None = None
    available_at: UTCDateTime | None = None


class StreakEvidence(StrictModel):
    snapshot_id: SnapshotId | None = None
    consecutive_successes: int = Field(default=0, ge=0)
    minutes: EvidenceMark = EvidenceMark.ABSENT
    usage: EvidenceMark = EvidenceMark.ABSENT
    matchup: EvidenceMark = EvidenceMark.ABSENT
    shot_quality: EvidenceMark = EvidenceMark.ABSENT
    health: EvidenceMark = EvidenceMark.ABSENT
    game_environment: EvidenceMark = EvidenceMark.ABSENT
    correlated_indicators: tuple[str, ...] = ()
    available_at: UTCDateTime | None = None


class ProjectionMiss(StrictModel):
    canonical_game_id: GameId
    canonical_projection_id: ProjectionId
    projection_available_at: UTCDateTime
    game_tip_at: UTCDateTime
    is_today_projection: bool = False
    missed: bool = True
    snapshot_id: SnapshotId | None = None


class VolumeStability(StrictModel):
    minutes: bool | None = None
    usage: bool | None = None
    shot_volume: bool | None = None
    snapshot_id: SnapshotId | None = None


class UnderperformanceEvidence(StrictModel):
    misses: tuple[ProjectionMiss, ...] = ()
    stability: VolumeStability | None = None
    injury: CauseState = CauseState.MISSING
    illness: CauseState = CauseState.MISSING
    rest: CauseState = CauseState.MISSING
    reduced_minutes: CauseState = CauseState.MISSING
    blowouts: CauseState = CauseState.MISSING
    declining_production_drivers: CauseState = CauseState.MISSING
    snapshot_id: SnapshotId | None = None
    available_at: UTCDateTime | None = None
