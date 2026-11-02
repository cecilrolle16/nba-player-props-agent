"""Feature snapshot for one player, game, and market.

Historical minutes on this record are observed playing time. They are not
the expected minutes supplied to the rules. Usage, shot volume, pace,
defense, and lineup stay null until their own inputs exist.
"""

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
from features.formulas import box_score_summary

FEATURE_VERSION = "2026-11-01.historical-box-score"


class FeatureGameUse(StrictModel):
    """One game the feature builder included or excluded."""

    canonical_game_id: GameId
    tip_at: UTCDateTime | None = None
    reason: str = Field(min_length=1)
    log_snapshot_id: SnapshotId | None = None
    schedule_snapshot_id: SnapshotId | None = None
    minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    points: int | None = Field(default=None, ge=0)
    assists: int | None = Field(default=None, ge=0)
    rebounds: int | None = Field(default=None, ge=0)


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
    feature_version: str | None = None
    window_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    max_games: int | None = Field(default=None, ge=1)
    lookback_days: int | None = Field(default=None, ge=1)
    min_games: int | None = Field(default=None, ge=1)
    defaults_validated: Literal[False] | None = None
    included_games: tuple[FeatureGameUse, ...] = ()
    excluded_games: tuple[FeatureGameUse, ...] = ()
    sample_size: int | None = Field(default=None, ge=0)
    total_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_points: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_assists: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_rebounds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    average_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    average_points: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    average_assists: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    average_rebounds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    points_per_minute: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    assists_per_minute: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rebounds_per_minute: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    points_variability: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    assists_variability: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rebounds_variability: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    insufficient_history: bool | None = None
    missing_minutes: bool = False
    missing_points: bool = False
    missing_assists: bool = False
    missing_rebounds: bool = False
    usage: None = None
    shot_volume: None = None
    pace: None = None
    defense: None = None
    lineup: None = None

    @model_validator(mode="after")
    def labels_match(self) -> "FeatureSnapshot":
        if self.stage_state is StageState.SYNTHETIC:
            if self.origin is not ValueOrigin.SYNTHETIC or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
                raise ValueError("synthetic features are uncalibrated")
            if self._calculated():
                raise ValueError("synthetic features do not carry calculated history")
        elif self.stage_state is StageState.CALCULATED:
            if self.origin is not ValueOrigin.OBSERVED or self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
                raise ValueError("calculated features are observed and are not a calibrated projection")
            if self.feature_version != FEATURE_VERSION or self.defaults_validated is not False:
                raise ValueError("calculated features record the unvalidated window version")
            if self.window_fingerprint is None or self.sample_size is None or self.insufficient_history is None:
                raise ValueError("calculated features record the window and the sample")
            self._matches_included_games()
        elif self.origin is not ValueOrigin.NOT_IMPLEMENTED or self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
            raise ValueError("an unfinished feature has no synthetic values")
        elif self._calculated():
            raise ValueError("an unfinished feature has no calculated history")
        return self

    def _calculated(self) -> bool:
        return any(
            (
                self.feature_version is not None,
                self.sample_size is not None,
                self.included_games,
                self.excluded_games,
                self.insufficient_history is not None,
                self.missing_minutes,
                self.missing_points,
                self.missing_assists,
                self.missing_rebounds,
            )
        )

    def _matches_included_games(self) -> None:
        if self.sample_size != len(self.included_games):
            raise ValueError("sample size is the number of included games")
        for game in self.included_games:
            if game.reason != "included" or None in (game.minutes, game.points, game.assists, game.rebounds):
                raise ValueError("an included game has a complete played box score")
            if game.minutes is not None and game.minutes <= 0:
                raise ValueError("an included game has positive minutes")
        summary = box_score_summary(
            tuple(game.minutes for game in self.included_games if game.minutes is not None),
            tuple(float(game.points) for game in self.included_games if game.points is not None),
            tuple(float(game.assists) for game in self.included_games if game.assists is not None),
            tuple(float(game.rebounds) for game in self.included_games if game.rebounds is not None),
        )
        for name, value in summary.items():
            actual = getattr(self, name)
            if value is None or actual is None:
                if value is not actual:
                    raise ValueError("feature totals do not match the included games")
            elif abs(float(actual) - float(value)) > 1e-9:
                raise ValueError("feature totals do not match the included games")
        if self.min_games is None or self.insufficient_history != (self.sample_size < self.min_games):
            raise ValueError("insufficient history follows the configured minimum")
