"""Build historical box-score features from completed games.

Games are ordered by tip time. Retrieval time does not decide the order.
The target game is excluded. A missing stat stays missing.
"""

from datetime import datetime, timedelta, timezone
import json
from hashlib import sha256

from contracts.common import GameId, GameStatus, PlayerId, SnapshotId
from contracts.configuration import FeatureWindowSettings
from contracts.features import FEATURE_VERSION, FeatureGameUse
from contracts.player_log import PlayerGameLog
from contracts.schedule import ScheduleGame
from features.formulas import box_score_summary

DEFAULT_WINDOWS = FeatureWindowSettings(
    version=1,
    max_games=10,
    lookback_days=30,
    min_games=5,
    defaults_validated=False,
)


class HistoricalAppearance:
    """One player-game observation. Null stats are missing, not zero."""

    __slots__ = (
        "canonical_player_id",
        "canonical_game_id",
        "tip_at",
        "status",
        "schedule_available_at",
        "schedule_snapshot_id",
        "log_available_at",
        "log_snapshot_id",
        "played",
        "minutes",
        "points",
        "assists",
        "rebounds",
        "forced_reason",
    )

    def __init__(
        self,
        *,
        canonical_player_id: PlayerId,
        canonical_game_id: GameId,
        tip_at: datetime | None,
        status: GameStatus | None,
        schedule_available_at: datetime | None,
        schedule_snapshot_id: SnapshotId | None,
        log_available_at: datetime | None,
        log_snapshot_id: SnapshotId | None,
        played: bool | None,
        minutes: float | None,
        points: int | None,
        assists: int | None,
        rebounds: int | None,
        forced_reason: str | None = None,
    ) -> None:
        self.canonical_player_id = canonical_player_id
        self.canonical_game_id = canonical_game_id
        self.tip_at = tip_at
        self.status = status
        self.schedule_available_at = schedule_available_at
        self.schedule_snapshot_id = schedule_snapshot_id
        self.log_available_at = log_available_at
        self.log_snapshot_id = log_snapshot_id
        self.played = played
        self.minutes = minutes
        self.points = points
        self.assists = assists
        self.rebounds = rebounds
        self.forced_reason = forced_reason


def window_fingerprint(windows: FeatureWindowSettings) -> str:
    payload = json.dumps(windows.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def appearances_from_logs(
    *,
    logs: tuple[PlayerGameLog, ...],
    log_snapshot_ids: tuple[SnapshotId, ...],
    target_game: ScheduleGame,
    history_games: tuple[ScheduleGame, ...],
) -> tuple[HistoricalAppearance, ...]:
    """Join accepted logs to schedule tips. A log without a game time stays incomplete."""

    schedules: dict[str, ScheduleGame | None] = {}

    def remember(game: ScheduleGame) -> None:
        prior = schedules.get(game.canonical_game_id, "missing")
        if prior == "missing":
            schedules[game.canonical_game_id] = game
        elif prior != game:
            schedules[game.canonical_game_id] = None

    remember(target_game)
    for game in history_games:
        remember(game)
    appearances: list[HistoricalAppearance] = []
    for index, log in enumerate(logs):
        snapshot_id = _log_snapshot(index, logs, log_snapshot_ids)
        game = schedules.get(log.canonical_game_id, "missing")
        if game == "missing" or game is None:
            appearances.append(
                HistoricalAppearance(
                    canonical_player_id=log.canonical_player_id,
                    canonical_game_id=log.canonical_game_id,
                    tip_at=None,
                    status=None,
                    schedule_available_at=None,
                    schedule_snapshot_id=None,
                    log_available_at=log.available_at,
                    log_snapshot_id=snapshot_id,
                played=True,
                minutes=log.minutes,
                points=log.points,
                assists=log.assists,
                rebounds=log.rebounds,
                forced_reason="missing_game_time" if game == "missing" else "conflicting_schedule",
                )
            )
            continue
        appearances.append(
            HistoricalAppearance(
                canonical_player_id=log.canonical_player_id,
                canonical_game_id=log.canonical_game_id,
                tip_at=game.tip_at,
                status=game.status,
                schedule_available_at=game.available_at,
                schedule_snapshot_id=game.snapshot_id,
                log_available_at=log.available_at,
                log_snapshot_id=snapshot_id,
                played=True,
                minutes=log.minutes,
                points=log.points,
                assists=log.assists,
                rebounds=log.rebounds,
            )
        )
    return tuple(appearances)


def build_historical_features(
    *,
    appearances: tuple[HistoricalAppearance, ...],
    target_player_id: PlayerId,
    target_game_id: GameId,
    target_tip_at: datetime,
    cutoff: datetime,
    windows: FeatureWindowSettings,
) -> dict[str, object]:
    """Return the calculated fields for one feature snapshot."""

    resolved = _resolve_duplicates(appearances)
    excluded: list[FeatureGameUse] = []
    eligible: list[HistoricalAppearance] = []
    missing_minutes = False
    missing_points = False
    missing_assists = False
    missing_rebounds = False
    for appearance, duplicate_reason in resolved:
        reason = duplicate_reason or _exclusion_reason(
            appearance,
            target_player_id=target_player_id,
            target_game_id=target_game_id,
            target_tip_at=target_tip_at,
            cutoff=cutoff,
            windows=windows,
        )
        if appearance.canonical_player_id == target_player_id and appearance.played is not False:
            missing_minutes = missing_minutes or appearance.minutes is None
            missing_points = missing_points or appearance.points is None
            missing_assists = missing_assists or appearance.assists is None
            missing_rebounds = missing_rebounds or appearance.rebounds is None
        if reason is None:
            eligible.append(appearance)
            continue
        excluded.append(_use(appearance, reason))
    eligible.sort(key=lambda item: (item.tip_at, item.canonical_game_id), reverse=True)
    kept = eligible[: windows.max_games]
    for appearance in eligible[windows.max_games :]:
        excluded.append(_use(appearance, "beyond_game_window"))
    kept.sort(key=lambda item: (item.tip_at, item.canonical_game_id))
    included = tuple(_use(appearance, "included") for appearance in kept)
    summary = box_score_summary(
        tuple(appearance.minutes for appearance in kept if appearance.minutes is not None),
        tuple(float(appearance.points) for appearance in kept if appearance.points is not None),
        tuple(float(appearance.assists) for appearance in kept if appearance.assists is not None),
        tuple(float(appearance.rebounds) for appearance in kept if appearance.rebounds is not None),
    )
    sample_size = int(summary["sample_size"] or 0)
    excluded.sort(key=lambda item: (item.tip_at is None, item.tip_at or cutoff, item.canonical_game_id, item.reason))
    return {
        "feature_version": FEATURE_VERSION,
        "window_fingerprint": window_fingerprint(windows),
        "max_games": windows.max_games,
        "lookback_days": windows.lookback_days,
        "min_games": windows.min_games,
        "defaults_validated": False,
        "included_games": included,
        "excluded_games": tuple(excluded),
        "insufficient_history": sample_size < windows.min_games,
        "missing_minutes": missing_minutes,
        "missing_points": missing_points,
        "missing_assists": missing_assists,
        "missing_rebounds": missing_rebounds,
        **summary,
    }


def _log_snapshot(
    index: int,
    logs: tuple[PlayerGameLog, ...],
    log_snapshot_ids: tuple[SnapshotId, ...],
) -> SnapshotId | None:
    if len(log_snapshot_ids) == len(logs):
        return log_snapshot_ids[index]
    if len(log_snapshot_ids) == 1:
        return log_snapshot_ids[0]
    return None


def _earliest_evidence(appearance: HistoricalAppearance) -> tuple[datetime, datetime]:
    far = datetime.max.replace(tzinfo=timezone.utc)
    return (appearance.log_available_at or far, appearance.schedule_available_at or far)


def _facts(appearance: HistoricalAppearance) -> tuple[object, ...]:
    return (
        appearance.tip_at,
        appearance.status,
        appearance.played,
        appearance.minutes,
        appearance.points,
        appearance.assists,
        appearance.rebounds,
        appearance.forced_reason,
    )


def _resolve_duplicates(
    appearances: tuple[HistoricalAppearance, ...],
) -> tuple[tuple[HistoricalAppearance, str | None], ...]:
    groups: dict[tuple[str, str], list[HistoricalAppearance]] = {}
    order: list[tuple[str, str]] = []
    for appearance in appearances:
        key = (appearance.canonical_player_id, appearance.canonical_game_id)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(appearance)
    resolved: list[tuple[HistoricalAppearance, str | None]] = []
    for key in order:
        rows = groups[key]
        if any(_facts(row) != _facts(rows[0]) for row in rows[1:]):
            resolved.extend((row, "conflicting_duplicate") for row in rows)
            continue
        keeper = min(rows, key=_earliest_evidence)
        resolved.append((keeper, None))
        resolved.extend((row, "duplicate") for row in rows if row is not keeper)
    return tuple(resolved)


def _exclusion_reason(
    appearance: HistoricalAppearance,
    *,
    target_player_id: str,
    target_game_id: str,
    target_tip_at: datetime,
    cutoff: datetime,
    windows: FeatureWindowSettings,
) -> str | None:
    if appearance.forced_reason is not None:
        return appearance.forced_reason
    if appearance.canonical_player_id != target_player_id:
        return "other_player"
    if appearance.canonical_game_id == target_game_id:
        return "target_game"
    if appearance.tip_at is None:
        return "missing_game_time"
    if appearance.tip_at >= target_tip_at:
        return "not_before_target"
    if appearance.tip_at > cutoff:
        return "after_cutoff"
    if appearance.status is not GameStatus.FINAL:
        return "not_completed"
    if appearance.schedule_available_at is None or appearance.schedule_available_at > cutoff:
        return "schedule_not_yet_available"
    if appearance.log_available_at is None or appearance.log_available_at > cutoff:
        return "log_not_yet_available"
    if appearance.played is False and appearance.minutes is not None and appearance.minutes > 0:
        return "conflicting_playing_time"
    if appearance.played is False:
        return "did_not_play"
    if appearance.minutes is None:
        return "missing_minutes"
    if appearance.minutes == 0:
        return "zero_minutes"
    if appearance.points is None:
        return "missing_points"
    if appearance.assists is None:
        return "missing_assists"
    if appearance.rebounds is None:
        return "missing_rebounds"
    earliest = target_tip_at - timedelta(days=windows.lookback_days)
    if appearance.tip_at < earliest:
        return "outside_lookback"
    return None


def _use(appearance: HistoricalAppearance, reason: str) -> FeatureGameUse:
    return FeatureGameUse(
        canonical_game_id=appearance.canonical_game_id,
        tip_at=appearance.tip_at,
        reason=reason,
        log_snapshot_id=appearance.log_snapshot_id,
        schedule_snapshot_id=appearance.schedule_snapshot_id,
        minutes=appearance.minutes,
        points=appearance.points,
        assists=appearance.assists,
        rebounds=appearance.rebounds,
    )
