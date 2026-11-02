"""Hand-checked baseline means. These tests do not build a distribution or a recommendation."""

from datetime import datetime, timedelta, timezone
import math

from pydantic import ValidationError

from contracts.common import AnalysisState, GameStatus, Market
from contracts.configuration import BaselineProjectionSettings, FeatureWindowSettings, load_baseline_projection
from contracts.features import FeatureSnapshot
from contracts.pipeline import PipelineInput
from contracts.player_log import PlayerGameLog
from contracts.projection import ExpectedMinutesInput, RatePrior
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from contracts.stages import CalibrationStatus, StageState, ValueOrigin
from features.historical import HistoricalAppearance, build_historical_features
from projection.baseline import DEFAULT_BASELINE, project_baseline
from pipeline.run import run_pipeline
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


def _played(game_id: str, tip: datetime, points: int, assists: int, rebounds: int, minutes: float) -> HistoricalAppearance:
    return HistoricalAppearance(
        canonical_player_id=PLAYER,
        canonical_game_id=game_id,
        tip_at=tip,
        status=GameStatus.FINAL,
        schedule_available_at=KNOWN,
        schedule_snapshot_id=f"snapshot:{game_id.removeprefix('game:')}-schedule",
        log_available_at=KNOWN,
        log_snapshot_id=f"snapshot:{game_id.removeprefix('game:')}-log",
        played=True,
        minutes=minutes,
        points=points,
        assists=assists,
        rebounds=rebounds,
    )


def _history() -> tuple[HistoricalAppearance, ...]:
    return (
        _played("game:synthetic-g1", datetime(2026, 10, 20, tzinfo=timezone.utc), 10, 2, 4, 20),
        _played("game:synthetic-g2", datetime(2026, 10, 27, tzinfo=timezone.utc), 20, 4, 8, 30),
        _played("game:synthetic-g3", datetime(2026, 11, 1, tzinfo=timezone.utc), 30, 6, 12, 10),
    )


def _features(market: Market, appearances: tuple[HistoricalAppearance, ...], windows: FeatureWindowSettings = WINDOWS) -> FeatureSnapshot:
    calculated = build_historical_features(
        appearances=appearances,
        target_player_id=PLAYER,
        target_game_id=TARGET,
        target_tip_at=TARGET_TIP,
        cutoff=CUTOFF,
        windows=windows,
    )
    return FeatureSnapshot(
        feature_id=f"feature:historical-{market.value}",
        canonical_player_id=PLAYER,
        canonical_game_id=TARGET,
        market=market,
        as_of=CUTOFF,
        schedule_snapshot_id="snapshot:synthetic-schedule-1",
        schedule_tip_at=TARGET_TIP,
        roster_available_at=KNOWN,
        quote_snapshot_id="snapshot:synthetic-points-1",
        quote_captured_at=KNOWN,
        quote_available_at=KNOWN,
        rules_version="2026-10-29.nba-props-angles",
        model_version="none",
        config_fingerprint="0" * 64,
        stage_state=StageState.CALCULATED,
        origin=ValueOrigin.OBSERVED,
        calibration_status=CalibrationStatus.NOT_APPLICABLE,
        available_at=CUTOFF,
        **calculated,
    )


def _minutes(
    market: Market,
    expected: float | None = 32,
    *,
    player: str = PLAYER,
    game: str = TARGET,
    known: bool = True,
    available_at: datetime | None = KNOWN,
    snapshot_id: str | None = "snapshot:synthetic-minutes-1",
    uncertainty: float | None = None,
) -> ExpectedMinutesInput:
    return ExpectedMinutesInput(
        canonical_player_id=player,
        canonical_game_id=game,
        market=market,
        known=known,
        expected_minutes=expected,
        minutes_uncertainty=uncertainty,
        snapshot_id=snapshot_id,
        available_at=available_at,
    )


def _prior(market: Market, rate: float, minutes: float = 20, *, player: str = PLAYER, available_at: datetime = KNOWN) -> RatePrior:
    return RatePrior(
        canonical_player_id=player,
        canonical_game_id=TARGET,
        market=market,
        prior_rate=rate,
        prior_minutes=minutes,
        snapshot_id="snapshot:synthetic-prior-1",
        available_at=available_at,
        source_ids=({"provider_id": "synthetic-priors", "native_id": f"prior-{market.value}"},),
    )


def _project(features: FeatureSnapshot, minutes: ExpectedMinutesInput | None, **kwargs: object) -> dict[str, object]:
    return project_baseline(
        features=features,
        minutes=minutes,
        prior=kwargs.get("prior"),
        shrinkage_enabled=bool(kwargs.get("shrinkage_enabled", False)),
        cutoff=CUTOFF,
    )


def test_unshrunk_means_for_points_assists_and_rebounds():
    features = {market: _features(market, _history()) for market in Market}
    expected = {
        Market.POINTS: (1.0, 32.0),
        Market.ASSISTS: (0.2, 6.4),
        Market.REBOUNDS: (0.4, 12.8),
    }
    for market, (rate, mean) in expected.items():
        result = _project(features[market], _minutes(market, uncertainty=1.5))
        assert result["baseline_label"] == "unshrunk"
        assert result["outcomes"] == ()
        assert result["coefficients_validated"] is False
        assert math.isclose(result["historical_rate"], rate)
        assert math.isclose(result["selected_rate"], rate)
        assert math.isclose(result["mean"], mean)
        assert result["minutes_uncertainty"] == 1.5
        assert result["expected_minutes"] == 32
        assert features[market].average_minutes == 20
        assert result["expected_minutes"] != features[market].average_minutes
        quiet = _project(features[market], _minutes(market, uncertainty=None))
        assert quiet["mean"] == result["mean"]


def test_shrinkage_uses_only_the_supplied_prior():
    points = _features(Market.POINTS, _history())
    shrunk = _project(points, _minutes(Market.POINTS), prior=_prior(Market.POINTS, 0.5), shrinkage_enabled=True)
    assert math.isclose(shrunk["selected_rate"], 0.875)
    assert math.isclose(shrunk["mean"], 28)
    assert shrunk["baseline_label"] == "shrunk"
    assert shrunk["prior_rate"] == 0.5
    assert shrunk["prior_minutes"] == 20
    ignored = _project(points, _minutes(Market.POINTS), prior=_prior(Market.POINTS, 0.5), shrinkage_enabled=False)
    assert ignored["mean"] == 32
    assert ignored["prior_rate"] is None
    assert ignored["baseline_label"] == "unshrunk"
    missing = _project(points, _minutes(Market.POINTS), shrinkage_enabled=True)
    assert missing["mean"] is None
    assert missing["missingness"] == "missing_prior"
    assert missing["outcomes"] == ()


def test_zero_production_is_a_zero_mean_and_bad_minutes_are_not():
    zero_game = (_played("game:synthetic-zero-box", datetime(2026, 11, 1, tzinfo=timezone.utc), 0, 0, 0, 20),)
    windows = WINDOWS.model_copy(update={"min_games": 1})
    points = _features(Market.POINTS, zero_game, windows)
    result = _project(points, _minutes(Market.POINTS, 30))
    assert result["historical_rate"] == 0
    assert result["mean"] == 0
    assert result["outcomes"] == ()
    assert result["missingness"] is None

    absent = _project(points, None)
    assert absent["missingness"] == "missing_expected_minutes"
    assert absent["mean"] is None
    invalid = _project(points, _minutes(Market.POINTS, 0))
    assert invalid["missingness"] == "invalid_expected_minutes"
    unknown = _project(points, _minutes(Market.POINTS, 30, known=False))
    assert unknown["missingness"] == "invalid_expected_minutes"
    assert unknown["mean"] is None


def test_insufficient_history_mismatch_and_future_evidence_have_no_mean():
    points = _features(Market.POINTS, _history(), WINDOWS.model_copy(update={"min_games": 5}))
    short = _project(points, _minutes(Market.POINTS))
    assert short["missingness"] == "insufficient_history"
    assert short["mean"] is None
    assert points.average_points == 20

    ready = _features(Market.POINTS, _history())
    mismatched = _project(ready, _minutes(Market.POINTS, player="player:synthetic-other"))
    assert mismatched["missingness"] == "identity_mismatch"
    other_stat = _project(ready, _minutes(Market.ASSISTS))
    assert other_stat["missingness"] == "identity_mismatch"
    future = _project(ready, _minutes(Market.POINTS, available_at=CUTOFF + timedelta(minutes=1)))
    assert future["missingness"] == "future_evidence"
    future_prior = _project(
        ready,
        _minutes(Market.POINTS),
        prior=_prior(Market.POINTS, 0.5, player="player:synthetic-other"),
        shrinkage_enabled=True,
    )
    assert future_prior["missingness"] == "identity_mismatch"
    late_prior = _project(
        ready,
        _minutes(Market.POINTS),
        prior=_prior(Market.POINTS, 0.5, available_at=CUTOFF + timedelta(minutes=1)),
        shrinkage_enabled=True,
    )
    assert late_prior["missingness"] == "future_evidence"
    assert late_prior["mean"] is None


def test_pipeline_baseline_stays_non_actionable():
    payload = load_json("synthetic_framework.json")
    subject = load_subject("valid_points.json")
    logs = []
    games = []
    for appearance in _history():
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
    bundle = PipelineInput(
        subject=subject,
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=tuple(logs),
        log_snapshot_ids=tuple(appearance.log_snapshot_id for appearance in _history()),
        history_games=tuple(games),
        feature_windows=WINDOWS,
        minutes_uncertainty=1.5,
        baseline_settings=DEFAULT_BASELINE,
        rate_prior=_prior(Market.POINTS, 0.5),
    )
    report = run_pipeline(bundle, catalog())
    assert report.projection.mean == 34
    assert report.projection.minutes_uncertainty == 1.5
    assert report.projection.baseline_label == "unshrunk"
    assert report.projection.outcomes == ()
    assert report.projection.model_version == "2026-11-01.baseline-minutes-rate"
    assert report.projection.calibration_status is CalibrationStatus.NOT_APPLICABLE
    assert report.validation.model_version == "none"
    assert report.comparison.stage_state is StageState.NOT_IMPLEMENTED
    assert report.decision.result == "pass"
    assert report.decision.reason == "not_implemented"
    assert report.recommendation == "none"
    assert report.live_recommendation is False
    assert [item.adjustment_amount for item in report.validation.audit.angles] == [None, None, None]

    shrunk_bundle = bundle.model_copy(
        update={
            "baseline_settings": BaselineProjectionSettings(
                version=1,
                shrinkage_enabled=True,
                coefficients_validated=False,
            )
        }
    )
    shrunk = run_pipeline(shrunk_bundle, catalog())
    assert math.isclose(shrunk.projection.mean, 29.75)
    assert shrunk.projection.baseline_label == "shrunk"
    assert shrunk.projection.outcomes == ()
    assert shrunk.recommendation == "none"


def test_shipped_baseline_does_not_invent_a_prior():
    status, parsed = load_baseline_projection(ROOT / "config" / "baseline-projection.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert parsed == DEFAULT_BASELINE
    assert parsed.shrinkage_enabled is False
    assert parsed.coefficients_validated is False
    try:
        BaselineProjectionSettings.model_validate(
            {"version": 1, "shrinkage_enabled": True, "coefficients_validated": True}
        )
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True
