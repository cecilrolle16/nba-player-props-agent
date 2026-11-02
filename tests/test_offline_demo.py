"""The offline demo runs the implemented stages on labeled synthetic data."""

import json
import os
import subprocess
import sys

from pipeline.demo import JSON_MARKER, run_demo
from support import ROOT

SENTINEL = "demo-sentinel-secret-not-a-key"


def _scenarios(document: dict) -> dict[str, dict]:
    return {item["scenario_id"]: item for item in document["scenarios"]}


def test_demo_outcomes_are_calculated_and_non_actionable():
    document = run_demo()
    assert document["data_label"] == "synthetic"
    assert document["credentials_read"] is False
    assert document["live_recommendation"] is False
    assert document["stake"] is None
    assert document["bankroll_sizing"] is None
    assert document["hypothesis_adjustments_enabled"] is False
    assert document["final_outcome_record"] == "research_decision"
    assert "Real-data evaluation" in document["remaining_gaps"][0]
    assert any("Minutes uncertainty" in item for item in document["remaining_gaps"])
    assert any("injury" in item.lower() for item in document["remaining_gaps"])
    assert any("Settlement grading" in item for item in document["remaining_gaps"])
    assert any("AWS" in item for item in document["remaining_gaps"])

    scenarios = _scenarios(document)
    candidate = scenarios["research_candidate"]
    no_bet = scenarios["no_bet"]
    unavailable = scenarios["missing_essential_evidence"]

    assert candidate["final_research_outcome"]["result"] == "research_candidate"
    assert candidate["final_research_outcome"]["reason"] == "all_research_gates_passed"
    assert candidate["final_research_outcome"]["calibration_status"] == "uncalibrated"
    assert candidate["final_research_outcome"]["data_quality"] == "available"
    assert candidate["input_eligibility_record"]["result"] == "pass"
    assert candidate["input_eligibility_record"]["not_a_research_outcome"] is True
    assert candidate["input_eligibility_record"]["edge"] is None
    assert candidate["input_eligibility_record"]["ev_per_unit"] is None
    assert [item["policy_id"] for item in candidate["rule_audits"]] == [
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    ]
    assert [item["adjustment_amount"] for item in candidate["rule_audits"]] == [None, None, None]
    stages = candidate["stages"]
    assert stages["features"]["stage_state"] == "calculated"
    assert stages["features"]["sample_size"] == 5
    assert stages["features"]["data_label"] == "synthetic"
    assert stages["baseline"]["stage_state"] == "calculated"
    assert stages["baseline"]["mean"] == 34
    assert stages["baseline"]["minutes_uncertainty"] is None
    assert stages["baseline"]["baseline_label"] == "unshrunk"
    assert stages["distribution"]["used"] is True
    assert stages["probabilities"]["p_win"] == 1
    assert stages["probabilities"]["p_loss"] == 0
    assert stages["probabilities"]["p_push"] == 0
    assert stages["probabilities"]["implied_probability"] is None
    price = stages["price"]
    assert price["status"] == "available"
    assert price["edge_benchmark"] == "raw_implied"
    assert price["ev_per_unit"] > 0.01
    assert price["ev_per_unit"] < 1
    assert price["probability_edge"] > 0.01
    assert price["stake"] is None
    assert price["live_recommendation"] is False
    assert "ev_per_unit" not in candidate["final_research_outcome"]
    assert candidate["final_research_outcome"]["price_id"] == price["price_id"]
    assert candidate["source_references"]["audit_id"]
    assert candidate["calibration_status"]["features"] == "not_applicable"
    assert candidate["calibration_status"]["research_decision"] == "uncalibrated"

    assert no_bet["final_research_outcome"]["result"] == "no_bet"
    assert no_bet["final_research_outcome"]["reason"] == "ev_below_minimum"
    assert no_bet["stages"]["price"]["ev_per_unit"] == price["ev_per_unit"]
    assert no_bet["input_eligibility_record"]["result"] == "pass"
    assert no_bet["final_research_outcome"]["stake"] is None
    assert no_bet["final_research_outcome"]["live_recommendation"] is False

    assert unavailable["final_research_outcome"]["result"] == "unavailable"
    assert unavailable["final_research_outcome"]["reason"] == "missing_essential_evidence"
    assert unavailable["final_research_outcome"]["data_quality"] == "unavailable"
    assert unavailable["stages"]["features"]["stage_state"] == "unavailable"
    assert unavailable["stages"]["baseline"]["mean"] is None
    assert unavailable["stages"]["distribution"]["used"] is False
    assert unavailable["stages"]["probabilities"]["p_win"] is None
    assert unavailable["stages"]["price"] is None
    assert unavailable["input_eligibility_record"]["result"] == "abstain"
    assert unavailable["input_eligibility_record"]["reason"] == "missing_essential_evidence"
    assert len(unavailable["rule_audits"]) == 3


def test_demo_json_is_deterministic():
    first = json.dumps(run_demo(), sort_keys=True)
    second = json.dumps(run_demo(), sort_keys=True)
    parsed = json.loads(first)
    assert first == second
    assert parsed["scenarios"][0]["final_research_outcome"]["result"] == "research_candidate"


def test_demo_command_prints_valid_json_without_credentials():
    env = os.environ.copy()
    env["ODDS_API_KEY"] = SENTINEL
    env["THE_ODDS_API_KEY"] = SENTINEL
    env["AWS_SECRET_ACCESS_KEY"] = SENTINEL
    completed = subprocess.run(
        [sys.executable, str(ROOT / "demo.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert SENTINEL not in completed.stdout
    assert SENTINEL not in completed.stderr
    assert "Final research outcome: research_candidate" in completed.stdout
    assert "Final research outcome: no_bet" in completed.stdout
    assert "Final research outcome: unavailable" in completed.stdout
    assert "not a research outcome" in completed.stdout
    summary, marker, payload = completed.stdout.partition(f"\n{JSON_MARKER}\n")
    assert marker
    assert "research_candidate" in summary
    document = json.loads(payload)
    assert [item["final_research_outcome"]["result"] for item in document["scenarios"]] == [
        "research_candidate",
        "no_bet",
        "unavailable",
    ]
    again = subprocess.run(
        [sys.executable, str(ROOT / "demo.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert again.stdout == completed.stdout
