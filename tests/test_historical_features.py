"""Hand-checked historical box scores. These tests do not project or price a bet."""

from datetime import datetime, timezone
import math
import socket

from contracts.common import AnalysisState, GameStatus
from contracts.configuration import FeatureWindowSettings, load_feature_windows
from contracts.features import FeatureSnapshot
from contracts.player_log import PlayerGameLog
from contracts.schedule import ScheduleGame
from contracts.stages import CalibrationStatus, StageState, ValueOrigin
from features.historical import (
    DEFAULT_WINDOWS,
    HistoricalAppearance,
    appearances_from_logs,
    build_historical_features,
)
from pipeline.run import run_pipeline
from pipeline.stages import reference_features
from support import ROOT, catalog, load_json, load_subject

PLAYER = "player:synthetic-guard-a"
TARGET = "game:synthetic-2026-11-01-home-away"
CUTOFF = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
TARGET_TIP = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
KNOWN = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
WINDOWS = FeatureWindowSettings(
    version=1,
    max_games=10,
    lookback_days=30,
    min_games=3,
    defaults_validated=False,
)


def _appearance(
    game_id: str,
    tip: datetime | None,
    *,
    points: int | None,
    assists: int | None,
    rebounds: int | None,
    minutes: float | None,
    played: bool | None = True,
    status: GameStatus | None = GameStatus.FINAL,
    log_available_at: datetime | None = KNOWN,
    schedule_available_at: datetime | None = KNOWN,
    player_id: str = PLAYER,
    forced_reason: str | None = None,
) -> HistoricalAppearance:
    return HistoricalAppearance(
        canonical_player_id=player_id,
        canonical_game_id=game_id,
        tip_at=tip,
        status=status,
        schedule_available_at=schedule_available_at,
        schedule_snapshot_id=f"snapshot:{game_id.removeprefix('game:')}-schedule",
        log_available_at=log_available_at,
        log_snapshot_id=f"snapshot:{game_id.removeprefix('game:')}-log",
        played=played,
        minutes=minutes,
        points=points,
        assists=assists,
        rebounds=rebounds,
        forced_reason=forced_reason,
    )


def _played(game_id: str, tip: datetime, points: int, assists: int, rebounds: int, minutes: float) -> HistoricalAppearance:
    return _appearance(game_id, tip, points=points, assists=assists, rebounds=rebounds, minutes=minutes)


def _sample() -> tuple[HistoricalAppearance, ...]:
    oldest = _played("game:synthetic-g1", datetime(2026, 10, 20, tzinfo=timezone.utc), 10, 2, 4, 20)
    middle = _played("game:synthetic-g2", datetime(2026, 10, 27, tzinfo=timezone.utc), 20, 4, 8, 30)
    newest = _played("game:synthetic-g3", datetime(2026, 11, 1, tzinfo=timezone.utc), 30, 6, 12, 10)
    newest.log_available_at = datetime(2026, 10, 31, tzinfo=timezone.utc)
    return (newest, oldest, middle)


def _build(appearances: tuple[HistoricalAppearance, ...], windows: FeatureWindowSettings = WINDOWS) -> dict[str, object]:
    return build_historical_features(
        appearances=appearances,
        target_player_id=PLAYER,
        target_game_id=TARGET,
        target_tip_at=TARGET_TIP,
        cutoff=CUTOFF,
        windows=windows,
    )


def test_three_games_match_the_hand_calculation_in_tip_order():
    result = _build(_sample())
    included = result["included_games"]
    assert [game.canonical_game_id for game in included] == [
        "game:synthetic-g1",
        "game:synthetic-g2",
        "game:synthetic-g3",
    ]
    assert result["sample_size"] == 3
    assert result["total_minutes"] == 60
    assert result["total_points"] == 60
    assert result["total_assists"] == 12
    assert result["total_rebounds"] == 24
    assert result["average_minutes"] == 20
    assert result["average_points"] == 20
    assert result["average_assists"] == 4
    assert result["average_rebounds"] == 8
    assert result["points_per_minute"] == 1
    assert result["assists_per_minute"] == 0.2
    assert result["rebounds_per_minute"] == 0.4
    assert result["points_variability"] == 10
    assert result["assists_variability"] == 2
    assert result["rebounds_variability"] == 4
    assert result["insufficient_history"] is False
    assert result["defaults_validated"] is False


def test_target_future_duplicate_dnp_zero_and_missing_stay_out_of_the_sample():
    rows = _sample()
    target = _played(TARGET, TARGET_TIP, 99, 9, 9, 40)
    leaked = _played(
        "game:synthetic-leak",
        datetime(2026, 10, 28, tzinfo=timezone.utc),
        100,
        10,
        10,
        40,
    )
    leaked.log_available_at = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
    after_cutoff = _played(
        "game:synthetic-later",
        datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc),
        80,
        8,
        8,
        30,
    )
    early = rows[1]
    late_copy = _played("game:synthetic-g1", early.tip_at, 10, 2, 4, 20)
    late_copy.log_available_at = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
    conflict_a = _played("game:synthetic-conflict", datetime(2026, 10, 21, tzinfo=timezone.utc), 11, 1, 1, 15)
    conflict_b = _played("game:synthetic-conflict", datetime(2026, 10, 21, tzinfo=timezone.utc), 15, 1, 1, 15)
    dnp = _appearance(
        "game:synthetic-dnp",
        datetime(2026, 10, 22, tzinfo=timezone.utc),
        points=None,
        assists=None,
        rebounds=None,
        minutes=None,
        played=False,
    )
    zero = _appearance(
        "game:synthetic-zero",
        datetime(2026, 10, 23, tzinfo=timezone.utc),
        points=0,
        assists=0,
        rebounds=0,
        minutes=0,
        played=True,
    )
    missing = _appearance(
        "game:synthetic-missing",
        datetime(2026, 10, 24, tzinfo=timezone.utc),
        points=12,
        assists=3,
        rebounds=None,
        minutes=18,
        played=True,
    )
    unfinished = _appearance(
        "game:synthetic-live",
        datetime(2026, 10, 25, tzinfo=timezone.utc),
        points=40,
        assists=4,
        rebounds=4,
        minutes=30,
        status=GameStatus.SCHEDULED,
    )
    old = _played("game:synthetic-old", datetime(2026, 9, 1, tzinfo=timezone.utc), 70, 7, 7, 35)
    other = _played("game:synthetic-other", datetime(2026, 10, 26, tzinfo=timezone.utc), 50, 5, 5, 25)
    other.canonical_player_id = "player:synthetic-other"
    result = _build(
        rows
        + (
            target,
            leaked,
            after_cutoff,
            late_copy,
            conflict_a,
            conflict_b,
            dnp,
            zero,
            missing,
            unfinished,
            old,
            other,
        )
    )
    assert result["average_points"] == 20
    assert result["sample_size"] == 3
    reasons = {game.canonical_game_id: game.reason for game in result["excluded_games"]}
    assert reasons[TARGET] == "target_game"
    assert reasons["game:synthetic-leak"] == "log_not_yet_available"
    assert reasons["game:synthetic-later"] == "after_cutoff"
    assert reasons["game:synthetic-g1"] == "duplicate"
    assert reasons["game:synthetic-conflict"] == "conflicting_duplicate"
    assert reasons["game:synthetic-dnp"] == "did_not_play"
    assert reasons["game:synthetic-zero"] == "zero_minutes"
    assert reasons["game:synthetic-missing"] == "missing_rebounds"
    assert reasons["game:synthetic-live"] == "not_completed"
    assert reasons["game:synthetic-old"] == "outside_lookback"
    assert reasons["game:synthetic-other"] == "other_player"
    assert result["missing_rebounds"] is True
    assert result["missing_minutes"] is False
    assert result["missing_points"] is False
    assert 0 not in (result["average_points"], result["total_points"])


def test_one_game_has_no_variability_and_empty_history_is_not_zero():
    only = (_sample()[1],)
    single = _build(only, windows=WINDOWS.model_copy(update={"min_games": 5}))
    assert single["sample_size"] == 1
    assert single["average_points"] == 10
    assert single["points_variability"] is None
    assert single["insufficient_history"] is True
    empty = _build(())
    assert empty["sample_size"] == 0
    assert empty["average_points"] is None
    assert empty["total_minutes"] is None
    assert empty["points_per_minute"] is None
    assert empty["points_variability"] is None
    assert empty["insufficient_history"] is True


def test_max_games_keeps_the_most_recent_tips():
    windows = WINDOWS.model_copy(update={"max_games": 2, "min_games": 2})
    result = _build(_sample(), windows=windows)
    assert [game.canonical_game_id for game in result["included_games"]] == [
        "game:synthetic-g2",
        "game:synthetic-g3",
    ]
    assert result["total_points"] == 50
    assert result["total_minutes"] == 40
    assert result["average_points"] == 25
    assert result["average_minutes"] == 20
    assert result["points_per_minute"] == 1.25
    assert math.isclose(result["points_variability"], math.sqrt(50))
    assert {game.reason for game in result["excluded_games"]} == {"beyond_game_window"}


def test_snapshot_rejects_a_total_that_disagrees_with_the_included_games():
    result = _build(_sample())
    shell = reference_features(_context())
    snapshot = shell.model_copy(
        update={
            "feature_id": "feature:historical-points",
            "stage_state": StageState.CALCULATED,
            "origin": ValueOrigin.OBSERVED,
            "calibration_status": CalibrationStatus.NOT_APPLICABLE,
            **result,
        }
    )
    assert isinstance(snapshot, FeatureSnapshot)
    assert snapshot.usage is None
    assert snapshot.shot_volume is None
    assert snapshot.pace is None
    assert snapshot.defense is None
    assert snapshot.lineup is None
    assert "expected_minutes" not in FeatureSnapshot.model_fields
    broken = snapshot.model_dump()
    broken["average_points"] = 99
    try:
        FeatureSnapshot.model_validate(broken)
    except ValueError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def test_pipeline_uses_history_without_making_a_recommendation(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    payload = load_json("synthetic_framework.json")
    subject = load_subject("valid_points.json")
    logs = []
    games = []
    for appearance in _sample():
        logs.append(
            PlayerGameLog(
                canonical_player_id=PLAYER,
                canonical_game_id=appearance.canonical_game_id,
                canonical_team_id="team:synthetic-home",
                points=appearance.points,
                assists=appearance.assists,
                rebounds=appearance.rebounds,
                minutes=appearance.minutes,
                available_at=appearance.log_available_at,
                source_ids=({"provider_id": "synthetic-logs", "native_id": appearance.canonical_game_id},),
            )
        )
        games.append(
            ScheduleGame(
                canonical_game_id=appearance.canonical_game_id,
                snapshot_id=appearance.schedule_snapshot_id,
                season="2026-27",
                home_team_id="team:synthetic-home",
                away_team_id="team:synthetic-away",
                status=GameStatus.FINAL,
                tip_at=appearance.tip_at,
                available_at=appearance.schedule_available_at,
                source_ids=({"provider_id": "synthetic-schedule", "native_id": appearance.canonical_game_id},),
            )
        )
    logs.append(PlayerGameLog.model_validate(payload["player_log"]))
    from contracts.pipeline import PipelineInput
    from contracts.roster import RosterEntry

    bundle = PipelineInput(
        subject=subject,
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=tuple(logs),
        log_snapshot_ids=tuple(appearance.log_snapshot_id for appearance in _sample()) + (payload["log_snapshot_id"],),
        history_games=tuple(games),
        feature_windows=WINDOWS,
    )
    report = run_pipeline(bundle, catalog())
    assert report.validation.state is AnalysisState.AVAILABLE
    assert [item.policy_id for item in report.validation.audit.angles] == [
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    ]
    assert report.features.average_points == 20
    assert report.features.average_minutes == 20
    assert report.features.average_minutes != subject.minutes.expected_minutes
    assert [game.canonical_game_id for game in report.features.included_games] == [
        "game:synthetic-g1",
        "game:synthetic-g2",
        "game:synthetic-g3",
    ]
    assert report.projection.stage_state is StageState.CALCULATED
    assert report.projection.mean == 34
    assert report.projection.outcomes == ()
    assert report.projection.expected_minutes == 34
    assert report.projection.baseline_label == "unshrunk"
    assert report.features.average_minutes == 20
    assert report.projection.mean != report.features.average_minutes
    assert report.decision.result == "pass"
    assert report.decision.reason == "not_implemented"
    assert report.recommendation == "none"
    assert report.live_recommendation is False


def test_ineligible_inputs_do_not_calculate_features():
    from contracts.pipeline import PipelineInput
    from contracts.roster import RosterEntry

    payload = load_json("synthetic_framework.json")
    report = run_pipeline(
        PipelineInput(
            subject=load_subject("valid_points.json").model_copy(update={"minutes": None}),
            schedule=ScheduleGame.model_validate(payload["schedule"]),
            roster=RosterEntry.model_validate(payload["roster"]),
            player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
            log_snapshot_ids=(payload["log_snapshot_id"],),
        ),
        catalog(),
    )
    assert report.features.stage_state is StageState.UNAVAILABLE
    assert report.features.sample_size is None
    assert report.features.average_points is None
    assert report.decision.result == "abstain"


def test_shipped_windows_match_the_unvalidated_defaults():
    status, parsed = load_feature_windows(ROOT / "config" / "features.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert parsed == DEFAULT_WINDOWS
    assert parsed.defaults_validated is False
    try:
        FeatureWindowSettings.model_validate(
            {"version": 1, "max_games": 10, "lookback_days": 30, "min_games": 5, "defaults_validated": True}
        )
    except ValueError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def _context():
    from contracts.pipeline import PipelineInput
    from contracts.roster import RosterEntry
    from pipeline.stages import StageContext

    payload = load_json("synthetic_framework.json")
    bundle = PipelineInput(
        subject=load_subject("valid_points.json"),
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
        log_snapshot_ids=(payload["log_snapshot_id"],),
    )
    rules = catalog()
    from rules.engine import evaluate

    validation = evaluate(rules, bundle.subject)
    return StageContext(bundle=bundle, catalog=rules, validation=validation, eligible=True)
