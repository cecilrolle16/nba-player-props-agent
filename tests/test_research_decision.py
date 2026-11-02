"""Research-policy gates. These tests do not measure a profitable threshold."""

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from contracts.common import AnalysisState
from contracts.configuration import ResearchDecisionSettings, load_research_decision
from contracts.pipeline import PipelineInput
from contracts.player_log import PlayerGameLog
from contracts.research import ResearchDecision
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from contracts.stages import CalibrationStatus
from decision.policy import DEFAULT_RESEARCH_POLICY, research_decision
from edge.price import price_quote
from pipeline.run import run_pipeline
from rules.engine import evaluate
from support import ROOT, catalog, load_json, load_subject
from test_edge import _comparison, _quote
from test_side_probability import _distribution

CUTOFF = datetime(2026, 11, 2, tzinfo=timezone.utc)


def _policy(**updates: object) -> ResearchDecisionSettings:
    payload = {
        "version": 1,
        "minimum_ev": 0.05,
        "minimum_probability_edge": 0.01,
        "edge_benchmark": "raw_implied",
        "configuration_label": "synthetic_test",
        "thresholds_validated": False,
        "recommendations_enabled": False,
        "hypothesis_adjustments_enabled": False,
    }
    payload.update(updates)
    return ResearchDecisionSettings.model_validate(payload)


def _validation(subject_name: str = "valid_points.json"):
    return evaluate(catalog(), load_subject(subject_name))


def _projection():
    payload = load_json("synthetic_framework.json")
    bundle = PipelineInput(
        subject=load_subject("valid_points.json"),
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
        log_snapshot_ids=(payload["log_snapshot_id"],),
    )
    return run_pipeline(bundle, catalog()).projection


def _decide(win: float, loss: float, push: float, policy: ResearchDecisionSettings | None = None, **price_updates: object):
    comparison = _comparison(win, loss, push)
    quote = _quote()
    if "quote_captured_at" in price_updates or "quote_available_at" in price_updates:
        comparison = _comparison(
            win,
            loss,
            push,
            quote_captured_at=price_updates.get("quote_captured_at", quote.captured_at),
            quote_available_at=price_updates.get("quote_available_at", quote.available_at),
        )
        quote = _quote(
            captured_at=price_updates.get("quote_captured_at", quote.captured_at),
            available_at=price_updates.get("quote_available_at", quote.available_at),
        )
    priced = price_quote(comparison, quote, decision_cutoff=CUTOFF)
    validation = _validation()
    decided = research_decision(
        validation=validation,
        comparison=comparison,
        projection=_projection(),
        price=priced,
        policy=policy if policy is not None else _policy(),
    )
    return priced, decided


def _gate(decided, gate_id: str):
    return next(gate for gate in decided.gates if gate.gate_id == gate_id)


def test_threshold_boundaries_and_nonpositive_prices():
    priced, decided = _decide(0.55, 0.40, 0.05)
    assert priced.ev_per_unit == 0.1
    assert decided.result == "research_candidate"
    assert decided.live_recommendation is False
    assert decided.stake is None
    assert decided.bankroll_sizing is None
    assert decided.calibration_status is CalibrationStatus.UNCALIBRATED
    assert decided.data_quality is AnalysisState.AVAILABLE
    assert decided.hit_probability_reference == priced.price_id
    assert "edge" not in ResearchDecision.model_fields
    assert "ev_per_unit" not in ResearchDecision.model_fields
    assert "implied_probability" not in ResearchDecision.model_fields
    assert decided.angle_policy_ids == (
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    )

    at_floor = _decide(
        0.55,
        0.40,
        0.05,
        policy=_policy(minimum_ev=priced.ev_per_unit, minimum_probability_edge=priced.probability_edge),
    )[1]
    assert at_floor.result == "research_candidate"
    assert _gate(at_floor, "minimum_ev").status == "passed"
    assert _gate(at_floor, "minimum_probability_edge").status == "passed"

    below_ev = _decide(0.55, 0.40, 0.05, policy=_policy(minimum_ev=0.2))[1]
    assert below_ev.result == "no_bet"
    assert _gate(below_ev, "minimum_ev").reason == "ev_below_minimum"
    assert below_ev.live_recommendation is False

    nonpositive = _decide(0.40, 0.55, 0.05)[1]
    assert nonpositive.result == "no_bet"
    assert _gate(nonpositive, "ev_positive").reason == "nonpositive_ev"
    assert _gate(nonpositive, "edge_positive").reason == "nonpositive_edge"


def test_missing_configuration_required_no_vig_all_push_and_stale_inputs():
    missing = _decide(
        0.55,
        0.40,
        0.05,
        policy=_policy(minimum_ev=None, minimum_probability_edge=None, edge_benchmark=None),
    )[1]
    assert missing.result == "unavailable"
    assert _gate(missing, "thresholds_configured").reason == "missing_thresholds"
    assert _gate(missing, "benchmark_configured").reason == "missing_edge_benchmark"
    assert _gate(missing, "ev_positive").status == "not_checked"

    no_vig = _decide(0.55, 0.40, 0.05, policy=_policy(edge_benchmark="no_vig"))[1]
    assert no_vig.result == "unavailable"
    assert _gate(no_vig, "benchmark_matched").reason == "required_no_vig_unavailable"
    assert _gate(no_vig, "minimum_probability_edge").status == "not_checked"
    assert no_vig.priced_edge_benchmark == "raw_implied"

    pushed = _decide(0.0, 0.0, 1.0)[1]
    assert pushed.result == "no_bet"
    assert _gate(pushed, "not_all_push").reason == "all_push"
    assert _gate(pushed, "ev_positive").reason == "nonpositive_ev"

    later = CUTOFF + timedelta(minutes=1)
    stale = _decide(0.55, 0.40, 0.05, quote_captured_at=later, quote_available_at=later)[1]
    assert stale.result == "unavailable"
    assert _gate(stale, "price_available").reason == "future_evidence"
    assert stale.data_quality is AnalysisState.AVAILABLE


def test_synthetic_origin_stays_synthetic_and_uncalibrated():
    priced, decided = _decide(0.55, 0.40, 0.05)
    assert decided.origin == "synthetic"
    assert decided.experimental is True
    assert decided.calibration_status is CalibrationStatus.UNCALIBRATED
    assert decided.result == "research_candidate"
    assert decided.recommendations_enabled is False
    assert decided.hypothesis_adjustments_enabled is False
    assert priced.raw_implied_probability is not None
    blocked = decided.model_dump()
    blocked["live_recommendation"] = True
    try:
        ResearchDecision.model_validate(blocked)
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def test_shipped_policy_is_unset():
    status, parsed = load_research_decision(ROOT / "config" / "research-decision.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert parsed == DEFAULT_RESEARCH_POLICY
    assert parsed is not None
    assert parsed.minimum_ev is None
    assert parsed.minimum_probability_edge is None
    assert parsed.edge_benchmark is None
    assert parsed.configuration_label == "unset"
    assert parsed.thresholds_validated is False
    try:
        ResearchDecisionSettings.model_validate(
            {
                "version": 1,
                "minimum_ev": 0.05,
                "minimum_probability_edge": 0.01,
                "edge_benchmark": "raw_implied",
                "configuration_label": "unset",
                "thresholds_validated": False,
                "recommendations_enabled": False,
                "hypothesis_adjustments_enabled": False,
            }
        )
    except ValidationError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def test_synthetic_pipeline_can_research_or_abstain_without_a_stake():
    distribution = _distribution(
        tuple(0.75 if stat == 30 else 0.25 if stat == 20 else 0.0 for stat in range(31)),
        player="player:synthetic-guard-a",
        game="game:synthetic-2026-11-01-home-away",
    )
    payload = load_json("synthetic_framework.json")
    base = dict(
        subject=load_subject("valid_points.json"),
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=(PlayerGameLog.model_validate(payload["player_log"]),),
        log_snapshot_ids=(payload["log_snapshot_id"],),
        residual_distribution=distribution,
        distribution_available_at=datetime(2026, 11, 1, 22, 50, tzinfo=timezone.utc),
    )
    candidate_run = run_pipeline(
        PipelineInput(**base, research_policy=_policy(minimum_ev=0.05, minimum_probability_edge=0.05)),
        catalog(),
    )
    research = candidate_run.research_decision
    assert research.result == "research_candidate"
    assert research.origin == "observed"
    assert research.calibration_status is CalibrationStatus.UNCALIBRATED
    assert research.data_quality is AnalysisState.AVAILABLE
    assert research.price_id == candidate_run.price.price_id
    assert research.hit_probability_reference == candidate_run.price.price_id
    assert candidate_run.price.ev_per_unit > 0.05
    assert candidate_run.comparison.implied_probability is None
    assert candidate_run.price.raw_implied_probability is not None
    assert candidate_run.decision.result == "pass"
    assert candidate_run.decision.edge is None
    assert candidate_run.decision.ev_per_unit is None
    assert candidate_run.decision.stake is None
    assert candidate_run.decision.live_recommendation is False
    assert candidate_run.recommendation == "none"
    assert [item.policy_id for item in candidate_run.validation.audit.angles] == list(research.angle_policy_ids)

    abstention = run_pipeline(
        PipelineInput(**base, research_policy=_policy(minimum_ev=1.0, minimum_probability_edge=0.05)),
        catalog(),
    )
    assert abstention.research_decision.result == "no_bet"
    assert _gate(abstention.research_decision, "minimum_ev").reason == "ev_below_minimum"
    assert abstention.decision.live_recommendation is False
    assert abstention.recommendation == "none"
    assert abstention.research_decision.stake is None

    unset = run_pipeline(PipelineInput(**base), catalog())
    assert unset.research_decision.result == "unavailable"
    assert _gate(unset.research_decision, "thresholds_configured").reason == "missing_thresholds"
    assert unset.recommendation == "none"

    ineligible = dict(base)
    ineligible["subject"] = load_subject("valid_points.json").model_copy(update={"minutes": None})
    blocked = run_pipeline(PipelineInput(**ineligible, research_policy=_policy()), catalog())
    assert blocked.research_decision.result == "unavailable"
    assert blocked.research_decision.data_quality is AnalysisState.UNAVAILABLE
    assert _gate(blocked.research_decision, "inputs_eligible").reason == "missing_essential_evidence"
    assert blocked.decision.result == "abstain"
    assert blocked.recommendation == "none"
