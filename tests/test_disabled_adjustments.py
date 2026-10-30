from contracts.analysis import RequestedAdjustment
from contracts.common import AnalysisState
from rules.engine import evaluate
from support import catalog, load_subject


def _angles(result):
    return tuple(item.policy_id for item in result.audit.angles)


def test_requested_hypothesis_deltas_stay_unset():
    subject = load_subject("valid_points.json").model_copy(
        update={
            "requested_adjustments": (
                RequestedAdjustment(adjustment_id="pace", delta=-1.25),
                RequestedAdjustment(adjustment_id="defender_return", delta=-2.0),
            )
        }
    )
    result = evaluate(catalog(), subject)
    assert result.state is AnalysisState.AVAILABLE
    assert [item.reason for item in result.adjustments] == ["future_context_model", "angle_magnitude_unset"]
    assert result.future_context[0].model_id == "pace"
    assert result.future_context[0].adjustment_amount is None
    assert result.future_context[0].validation_status == "not_modeled"
    for angle in result.audit.angles:
        assert angle.adjustment_amount is None
    assert catalog().numeric_hypothesis_adjustments_enabled is False
    assert set(catalog().hypothesis_magnitudes.values()) == {None}


def test_unknown_adjustment_is_unavailable():
    subject = load_subject("valid_points.json").model_copy(
        update={"requested_adjustments": (RequestedAdjustment(adjustment_id="clutch_factor", delta=3.0),)}
    )
    result = evaluate(catalog(), subject)
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "unknown_adjustment"
    assert _angles(result) == (
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    )


def test_enabling_numeric_adjustments_fails_closed():
    forced = catalog().model_copy(update={"numeric_hypothesis_adjustments_enabled": True})
    subject = load_subject("valid_points.json").model_copy(
        update={"requested_adjustments": (RequestedAdjustment(adjustment_id="pace", delta=-1.25),)}
    )
    result = evaluate(forced, subject)
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "adjustments_not_permitted"
    assert all(angle.adjustment_amount is None for angle in result.audit.angles)


def test_every_evaluation_audits_all_three_policies():
    blocked = evaluate(catalog(), load_subject("malformed_odds.json"))
    assert blocked.state is AnalysisState.UNAVAILABLE
    assert blocked.reason == "malformed_odds"
    assert _angles(blocked) == (
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    )
    for angle in blocked.audit.angles:
        assert angle.status
        assert angle.reason
        assert angle.adjustment_amount is None
        assert angle.validation_status in {"disabled", "unvalidated"}
        assert isinstance(angle.evidence_snapshot_ids, tuple)


def test_missing_minutes_prevents_an_actionable_result_without_skipping_as_stale():
    subject = load_subject("valid_points.json").model_copy(update={"minutes": None})
    result = evaluate(catalog(), subject)
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "missing_essential_evidence"
    assert result.essential_gaps == ("minutes",)
    assert result.state is not AnalysisState.SKIP


def test_missing_role_is_disclosed_and_the_analysis_can_continue():
    subject = load_subject("valid_points.json").model_copy(update={"role": None})
    result = evaluate(catalog(), subject)
    assert result.state is AnalysisState.AVAILABLE
    assert "role_evidence_missing" in result.disclosed_gaps


def test_injury_freshness_default_is_unvalidated_and_configurable():
    rules = catalog()
    assert rules.injury_freshness.max_age_minutes == 180
    assert rules.injury_freshness.validation_status == "unvalidated_operational_default"
    short = rules.model_copy(
        update={"injury_freshness": rules.injury_freshness.model_copy(update={"max_age_minutes": 30})}
    )
    result = evaluate(short, load_subject("valid_points.json"))
    assert result.state is AnalysisState.SKIP
    assert result.reason == "stale_injury_report"
    assert _angles(result) == (
        "defender_return",
        "no_streak_chasing",
        "underperformance_rebound",
    )
