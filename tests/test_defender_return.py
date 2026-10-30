from contracts.common import AnalysisState, InjuryStatus, Market
from contracts.evidence import DefenderReturnEvidence
from rules.engine import evaluate
from support import catalog, load_json, load_subject, subject_from

VERIFIED = {
    "return_date_snapshot_id": "snapshot:synthetic-return-date",
    "confirmed_return_date": "2026-10-20",
    "games_since_return": 4,
    "minutes_since_return": 110.0,
    "minutes_snapshot_id": "snapshot:synthetic-return-minutes",
    "key_defender_snapshot_id": "snapshot:synthetic-key-defender",
    "opponent_is_key_defender": True,
    "minutes_restriction": "restricted",
    "defensive_rotation_snapshot_id": "snapshot:synthetic-rotation",
    "defensive_rotation_support": "present",
    "normal_minutes_supported": False,
    "healthy_defensive_performance_supported": False,
    "available_at": "2026-11-01T22:00:00Z",
}


def _with_return(name: str = "valid_points.json", **updates):
    payload = dict(VERIFIED)
    payload.update(updates)
    subject = load_subject(name)
    return subject.model_copy(update={"defender_return": DefenderReturnEvidence.model_validate(payload)})


def _angle(result, policy_id="defender_return"):
    return next(item for item in result.audit.angles if item.policy_id == policy_id)


def test_verified_points_return_qualifies_without_a_magnitude():
    result = evaluate(catalog(), _with_return())
    angle = _angle(result)
    assert result.state is AnalysisState.AVAILABLE
    assert angle.status == "qualified"
    assert angle.reason == "return_facts_verified_magnitude_unset"
    assert angle.adjustment_amount is None
    assert angle.validation_status == "disabled"
    assert "snapshot:synthetic-key-defender" in angle.evidence_snapshot_ids


def test_missing_return_facts_skip_the_angle_only():
    result = evaluate(catalog(), _with_return(confirmed_return_date=None))
    angle = _angle(result)
    assert result.state is AnalysisState.AVAILABLE
    assert result.reason == "inputs_accepted"
    assert angle.status == "skipped"
    assert angle.reason == "missing_required_return_evidence"
    assert "confirmed_return_date" in angle.disclosed_gaps


def test_assists_and_rebounds_do_not_take_defender_return():
    assists = evaluate(catalog(), _with_return("defender_assists.json"))
    assert assists.state is AnalysisState.AVAILABLE
    assert assists.market is Market.ASSISTS
    assert _angle(assists).status == "not_applicable"
    assert _angle(assists).reason == "defender_return_not_applicable"
    assert _angle(assists).adjustment_amount is None

    payload = load_json("defender_assists.json")
    payload["market"] = "rebounds"
    payload["snapshot"]["market"] = "rebounds"
    payload["snapshot"]["snapshot_id"] = "snapshot:synthetic-rebounds-1"
    payload["historical_projections"][0]["market"] = "rebounds"
    payload["historical_projections"][0]["canonical_projection_id"] = "projection:synthetic-rebounds-hist-1"
    payload["defender_return"] = dict(VERIFIED)
    rebounds = evaluate(catalog(), subject_from(payload))
    assert rebounds.market is Market.REBOUNDS
    assert _angle(rebounds).status == "not_applicable"


def test_unclear_secondary_evidence_skips_unless_research_is_configured():
    unclear = _with_return(minutes_restriction="unclear", defensive_rotation_support="unclear")
    skipped = evaluate(catalog(), unclear)
    assert _angle(skipped).status == "skipped"
    assert _angle(skipped).reason == "unclear_secondary_evidence"
    assert skipped.state is AnalysisState.AVAILABLE

    rules = catalog()
    policy = rules.angle_policies.defender_return.model_copy(
        update={"flagged_research_when_secondary_unclear": True}
    )
    configured = rules.model_copy(
        update={"angle_policies": rules.angle_policies.model_copy(update={"defender_return": policy})}
    )
    flagged = evaluate(configured, unclear)
    assert _angle(flagged).status == "flagged_research"
    assert _angle(flagged).validation_status == "unvalidated"
    assert _angle(flagged).adjustment_amount is None


def test_normal_minutes_and_healthy_defense_reduce_without_a_number():
    result = evaluate(
        catalog(),
        _with_return(normal_minutes_supported=True, healthy_defensive_performance_supported=True),
    )
    angle = _angle(result)
    assert angle.status == "reduced"
    assert angle.reason == "normal_minutes_and_healthy_defense"
    assert angle.adjustment_amount is None


def test_injury_minutes_and_role_override_the_angle():
    injury = load_subject("valid_points.json").injury.model_copy(update={"status": InjuryStatus.QUESTIONABLE})
    injured = evaluate(catalog(), _with_return().model_copy(update={"injury": injury}))
    assert injured.state is AnalysisState.AVAILABLE
    assert _angle(injured).status == "overridden"
    assert "injury_news" in _angle(injured).disclosed_gaps

    minutes = load_subject("valid_points.json").minutes.model_copy(update={"changed": True})
    changed = evaluate(catalog(), _with_return().model_copy(update={"minutes": minutes}))
    assert _angle(changed).status == "overridden"

    role = load_subject("valid_points.json").role.model_copy(update={"changed": True})
    moved = evaluate(catalog(), _with_return().model_copy(update={"role": role}))
    assert _angle(moved).status == "overridden"
    assert moved.state is AnalysisState.AVAILABLE
