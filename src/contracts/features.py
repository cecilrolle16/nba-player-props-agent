"""Feature snapshot. This record stores references, not computed features."""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import (
    UTCDateTime,
    FeatureId,
    GameId,
    Market,
    PlayerId,
    SnapshotId,
    StrictModel,
)
from contracts.stages import CalibrationStatus, StageState, ValueOrigin


class FeatureSnapshot(StrictModel):
    feature_id: FeatureId
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    as_of: UTCDateTime
    schedule_snapshot_id: SnapshotId
    schedule_tip_at: UTCDateTime
    roster_available_at: UTCDateTime
    log_snapshot_ids: tuple[SnapshotId, ...] = ()
    evidence_snapshot_ids: tuple[SnapshotId, ...] = ()
    quote_snapshot_id: SnapshotId
    quote_captured_at: UTCDateTime
    quote_available_at: UTCDateTime
    rules_version: str = Field(min_length=1)
    model_version: Literal["none"]
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage_state: StageState
    origin: ValueOrigin
    calibration_status: CalibrationStatus
    available_at: UTCDateTime

    @model_validator(mode="after")
    def labels_match(self) -> "FeatureSnapshot":
        if self.stage_state is StageState.SYNTHETIC:
            if self.origin is not ValueOrigin.SYNTHETIC or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
                raise ValueError("synthetic features are uncalibrated")
        elif self.origin is not ValueOrigin.NOT_IMPLEMENTED or self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
            raise ValueError("an unfinished feature has no synthetic values")
        return self
