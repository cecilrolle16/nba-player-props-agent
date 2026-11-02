import socket

from pydantic import ValidationError

from contracts.common import AnalysisState
from contracts.comparison import MarketComparison
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution, StatMass
from contracts.pipeline import PipelineInput
from contracts.player_log import PlayerGameLog
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from contracts.settlement import SettlementOutcome, SettlementRecord
from contracts.stages import CalibrationStatus, StageState, ValueOrigin
from pipeline.run import run_pipeline
from pipeline.stages import PipelineStages, default_comparison, default_projection, reference_features
from support import catalog, load_json, load_subject


def _bundle(subject_name: str = "valid_points.json", **subject_updates: object) -> PipelineInput:
    payload = load_json("synthetic_framework.json")
    subject = load_subject(subject_name)
    if subject_updates:
        subject = subject.model_copy(update=subject_updates)
    return PipelineInput(
        subject=subject,
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
        log_snapshot_ids=(payload["log_snapshot_id"],),
    )


def _synthetic_stages() -> PipelineStages:
    def features(context):
        current = reference_features(context)
        return current.model_copy(
            update={
                "feature_id": "feature:synthetic-points",
                "stage_state": StageState.SYNTHETIC,
                "origin": ValueOrigin.SYNTHETIC,
                "calibration_status": CalibrationStatus.UNCALIBRATED,
            }
        )

    def projection(context, features):
        current = default_projection(context, features)
        return current.model_copy(
            update={
                "canonical_projection_id": "projection:synthetic-distribution",
                "feature_id": features.feature_id,
                "mean": 25.0,
                "outcomes": (StatMass(stat=25, probability=1.0),),
                "stage_state": StageState.SYNTHETIC,
                "origin": ValueOrigin.SYNTHETIC,
                "calibration_status": CalibrationStatus.UNCALIBRATED,
                "projected_at": context.bundle.subject.as_of,
                "available_at": context.bundle.subject.as_of,
            }
        )

    def comparison(context, projection):
        current = default_comparison(context, projection)
        return current.model_copy(
            update={
                "comparison_id": "comparison:synthetic-points",
                "canonical_projection_id": projection.canonical_projection_id,
                "line": 24.5,
                "side": "over",
                "book": "synthetic-book",
                "american_odds": "-110",
                "paired": False,
                "p_win": 0.52,
                "p_loss": 0.48,
                "p_push": 0.0,
                "stage_state": StageState.SYNTHETIC,
                "origin": ValueOrigin.SYNTHETIC,
                "calibration_status": CalibrationStatus.UNCALIBRATED,
            }
        )

    def decision(context, features, projection, comparison):
        del features
        validation = context.validation
        return Decision(
            decision_id="decision:synthetic-points",
            result="synthetic_candidate",
            reason="synthetic_uncalibrated",
            analysis_state=validation.state,
            inputs_eligible=context.eligible,
            edge=0.02,
            ev_per_unit=0.01,
            comparison_id=comparison.comparison_id,
            canonical_projection_id=projection.canonical_projection_id,
            odds_snapshot_id=comparison.odds_snapshot_id,
            calibration_status=CalibrationStatus.UNCALIBRATED,
            origin=ValueOrigin.SYNTHETIC,
            audit_id=validation.audit.audit_id,
            rules_version=context.catalog.rules_version,
            model_version="none",
            config_fingerprint=context.catalog.source_sha256,
            as_of=context.bundle.subject.as_of,
        )

    return PipelineStages(features=features, projection=projection, comparison=comparison, decision=decision)


def test_synthetic_inputs_pass_the_existing_gates():
    rules = catalog()
    report = run_pipeline(_bundle(), rules)
    assert report.validation.state is AnalysisState.AVAILABLE
    assert report.validation.reason == "inputs_accepted"
    assert report.inputs_eligible is True
    assert report.recommendation == "none"
    assert report.live_recommendation is False
    assert [item.policy_id for item in report.validation.audit.angles] == [
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    ]
    assert report.validation.model_version == "none"
    assert report.validation.rules_version == rules.rules_version
    assert report.features.config_fingerprint == rules.source_sha256
    assert report.features.quote_captured_at.isoformat() == "2026-11-01T22:40:00+00:00"
    assert report.features.schedule_tip_at.isoformat() == "2026-11-02T00:00:00+00:00"
    assert "snapshot:synthetic-availability-1" in report.features.evidence_snapshot_ids
    assert report.features.log_snapshot_ids == ("snapshot:synthetic-log-1",)


def test_injected_synthetic_stages_stay_uncalibrated(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    report = run_pipeline(_bundle(), catalog(), _synthetic_stages())
    assert report.decision.result == "synthetic_candidate"
    assert report.decision.origin is ValueOrigin.SYNTHETIC
    assert report.decision.calibration_status is CalibrationStatus.UNCALIBRATED
    assert report.projection.mean == 25.0
    assert report.projection.outcomes[0].stat == 25
    assert "p_win" not in ProjectionDistribution.model_fields
    assert report.comparison.p_win == 0.52
    assert report.comparison.p_loss == 0.48
    assert report.comparison.p_push == 0.0
    assert report.comparison.canonical_projection_id == report.projection.canonical_projection_id
    assert report.decision.comparison_id == report.comparison.comparison_id
    assert report.comparison.implied_probability is None
    assert report.comparison.no_vig_probability is None
    assert report.decision.edge == 0.02
    assert report.decision.ev_per_unit == 0.01
    assert report.decision.threshold is None
    assert report.decision.stake is None
    assert report.decision.bankroll_sizing is None
    assert report.decision.actionable is False
    assert report.live_recommendation is False
    assert report.recommendation == "none"
    assert report.settlement is None
    assert len(report.validation.audit.angles) == 3


def test_missing_essential_evidence_abstains():
    report = run_pipeline(_bundle(minutes=None), catalog())
    assert report.validation.state is AnalysisState.UNAVAILABLE
    assert report.validation.reason == "missing_essential_evidence"
    assert report.inputs_eligible is False
    assert report.decision.result == "abstain"
    assert report.decision.reason == "missing_essential_evidence"
    assert report.decision.actionable is False
    assert report.decision.edge is None
    assert len(report.validation.audit.angles) == 3


def test_unfinished_stages_cannot_become_a_recommendation():
    report = run_pipeline(_bundle(), catalog())
    assert report.inputs_eligible is True
    assert report.validation.state is AnalysisState.AVAILABLE
    assert report.features.stage_state is StageState.CALCULATED
    assert report.features.origin is ValueOrigin.OBSERVED
    assert report.features.sample_size == 0
    assert report.features.average_points is None
    assert report.features.insufficient_history is True
    assert report.features.excluded_games[0].reason == "target_game"
    assert report.projection.stage_state is StageState.UNAVAILABLE
    assert report.projection.origin is ValueOrigin.OBSERVED
    assert report.projection.outcomes == ()
    assert report.projection.mean is None
    assert report.projection.missingness == "insufficient_history"
    assert report.comparison.p_win is None
    assert report.comparison.stage_state is StageState.NOT_IMPLEMENTED
    assert report.decision.result == "pass"
    assert report.decision.reason == "not_implemented"
    assert report.decision.live_recommendation is False
    assert report.recommendation == "none"

    only_decision = PipelineStages(decision=_synthetic_stages().decision)
    guarded = run_pipeline(_bundle(), catalog(), only_decision)
    assert guarded.decision.result == "pass"
    assert guarded.decision.reason == "not_implemented"
    assert guarded.decision.edge is None


def test_synthetic_results_cannot_be_labeled_live():
    report = run_pipeline(_bundle(), catalog(), _synthetic_stages())
    assert report.decision.result == "synthetic_candidate"
    assert report.live_recommendation is False
    assert report.decision.actionable is False
    payload = report.decision.model_dump()
    payload["live_recommendation"] = True
    try:
        Decision.model_validate(payload)
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def test_settlement_contract_does_not_grade():
    record = SettlementRecord(
        settlement_id="settlement:synthetic-points",
        pregame_snapshot_id="snapshot:synthetic-points-1",
        canonical_player_id="player:synthetic-guard-a",
        canonical_game_id="game:synthetic-2026-11-01-home-away",
        market="points",
        side="over",
        line=24.5,
        outcome=SettlementOutcome.WIN,
        stat=30,
        rules_version="2026-10-29.nba-props-angles",
        model_version="none",
    )
    assert record.graded is False
    assert record.unit_profit is None
    assert record.outcome is SettlementOutcome.WIN
    voided = record.model_copy(update={"outcome": SettlementOutcome.VOID, "stat": None, "settlement_id": "settlement:synthetic-void"})
    pushed = record.model_copy(update={"outcome": SettlementOutcome.PUSH, "stat": 24, "line": 24.0, "settlement_id": "settlement:synthetic-push"})
    assert voided.outcome is SettlementOutcome.VOID
    assert pushed.outcome is SettlementOutcome.PUSH
    assert voided.outcome is not pushed.outcome
    assert voided.graded is False
    assert pushed.graded is False
    try:
        SettlementRecord.model_validate(record.model_dump() | {"unit_profit": 0.91, "graded": True})
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def _priced_comparison(projection_id: str, comparison_id: str, line: float, probabilities: tuple[float, float, float]) -> dict:
    p_win, p_loss, p_push = probabilities
    return {
        "comparison_id": comparison_id,
        "canonical_projection_id": projection_id,
        "canonical_player_id": "player:synthetic-guard-a",
        "canonical_game_id": "game:synthetic-2026-11-01-home-away",
        "market": "points",
        "line": line,
        "side": "over",
        "book": "synthetic-book",
        "american_odds": "-110",
        "odds_snapshot_id": "snapshot:synthetic-points-1",
        "paired": False,
        "p_win": p_win,
        "p_loss": p_loss,
        "p_push": p_push,
        "quote_captured_at": "2026-11-01T22:40:00Z",
        "quote_available_at": "2026-11-01T22:40:00Z",
        "rules_version": "2026-10-29.nba-props-angles",
        "model_version": "none",
        "stage_state": "synthetic",
        "origin": "synthetic",
        "calibration_status": "uncalibrated",
        "source_ids": [{"provider_id": "synthetic-markets", "native_id": "quote-points-1"}],
    }


def test_one_stat_distribution_supports_two_lines_and_probabilities_stay_attached():
    distribution = ProjectionDistribution(
        canonical_projection_id="projection:synthetic-distribution",
        feature_id="feature:synthetic-points",
        canonical_player_id="player:synthetic-guard-a",
        canonical_game_id="game:synthetic-2026-11-01-home-away",
        market="points",
        mean=25.0,
        outcomes=(StatMass(stat=22, probability=0.4), StatMass(stat=28, probability=0.6)),
        rules_version="2026-10-29.nba-props-angles",
        model_version="none",
        stage_state=StageState.SYNTHETIC,
        origin=ValueOrigin.SYNTHETIC,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        projected_at="2026-11-01T23:00:00Z",
        available_at="2026-11-01T23:00:00Z",
        source_ids=({"provider_id": "synthetic-projections", "native_id": "stat-mass"},),
    )
    lower = MarketComparison.model_validate(
        _priced_comparison(distribution.canonical_projection_id, "comparison:synthetic-line-a", 24.5, (0.55, 0.45, 0.0))
    )
    higher = MarketComparison.model_validate(
        _priced_comparison(distribution.canonical_projection_id, "comparison:synthetic-line-b", 27.5, (0.40, 0.60, 0.0))
    )
    assert lower.canonical_projection_id == higher.canonical_projection_id == distribution.canonical_projection_id
    assert lower.line != higher.line
    assert lower.p_win != higher.p_win
    assert "line" not in ProjectionDistribution.model_fields
    assert "side" not in ProjectionDistribution.model_fields

    detached = _priced_comparison(
        distribution.canonical_projection_id, "comparison:synthetic-detached", 24.5, (0.5, 0.5, 0.0)
    )
    detached["line"] = None
    detached["side"] = None
    try:
        MarketComparison.model_validate(detached)
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True
