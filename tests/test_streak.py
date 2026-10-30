from contracts.analysis import RequestedAdjustment
from contracts.common import AnalysisState
from contracts.evidence import EvidenceMark, StreakEvidence
from rules.engine import evaluate
from support import catalog, load_json, load_subject, subject_from


def _streak(**updates):
    payload = {
        "snapshot_id": "snapshot:synthetic-streak-1",
        "consecutive_successes": 4,
        "minutes": "absent",
        "usage": "absent",
        "matchup": "absent",
        "shot_quality": "absent",
        "health": "absent",
        "game_environment": "absent",
        "correlated_indicators": [],
        "available_at": "2026-11-01T22:00:00Z",
    }
    payload.update(updates)
    return load_subject("valid_points.json").model_copy(
        update={"streak": StreakEvidence.model_validate(payload)}
    )


def _angle(result):
    return next(item for item in result.audit.angles if item.policy_id == "no_streak_chasing")


def test_a_streak_alone_does_not_raise_projection_or_confidence():
    for market_file in ("valid_points.json", "defender_assists.json"):
        subject = load_subject(market_file).model_copy(update={"streak": _streak().streak})
        result = evaluate(catalog(), subject)
        angle = _angle(result)
        assert result.state is AnalysisState.AVAILABLE
        assert angle.status == "unsupported"
        assert angle.reason == "streak_alone_does_not_raise_projection"
        assert angle.adjustment_amount is None
        assert "confidence" not in result.model_dump()


def test_correlated_indicators_are_not_independent_evidence():
    subject = _streak(usage="independent", correlated_indicators=["usage"])
    result = evaluate(catalog(), subject)
    angle = _angle(result)
    assert angle.status == "unsupported"
    assert angle.reason == "streak_alone_does_not_raise_projection"


def test_independent_evidence_is_recorded_and_missing_categories_are_disclosed():
    subject = _streak(minutes=EvidenceMark.INDEPENDENT.value, shot_quality=EvidenceMark.ABSENT.value)
    result = evaluate(catalog(), subject)
    angle = _angle(result)
    assert angle.status == "disclosed"
    assert angle.reason == "independent_evidence_recorded_no_confidence_boost"
    assert "shot_quality" in angle.disclosed_gaps
    assert "minutes" not in angle.disclosed_gaps
    assert angle.adjustment_amount is None


def test_role_change_overrides_streak_support():
    subject = _streak(minutes="independent")
    role = subject.role.model_copy(update={"changed": True})
    result = evaluate(catalog(), subject.model_copy(update={"role": role}))
    assert result.state is AnalysisState.AVAILABLE
    assert _angle(result).status == "overridden"


def test_rebounds_streak_is_also_unsupported_without_independent_evidence():
    payload = load_json("defender_assists.json")
    payload["market"] = "rebounds"
    payload["snapshot"]["market"] = "rebounds"
    payload["snapshot"]["snapshot_id"] = "snapshot:synthetic-rebounds-1"
    payload["historical_projections"][0]["market"] = "rebounds"
    payload["historical_projections"][0]["canonical_projection_id"] = "projection:synthetic-rebounds-hist-1"
    payload["streak"] = {
        "snapshot_id": "snapshot:synthetic-streak-1",
        "consecutive_successes": 3,
        "available_at": "2026-11-01T22:00:00Z",
    }
    result = evaluate(catalog(), subject_from(payload))
    assert result.market.value == "rebounds"
    assert _angle(result).status == "unsupported"
