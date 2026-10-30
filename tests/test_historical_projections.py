from contracts.common import AnalysisState
from rules.engine import evaluate
from support import catalog, load_json, load_subject, subject_from


def test_empty_history_is_unavailable():
    payload = load_json("valid_points.json")
    payload["historical_projections"] = []
    result = evaluate(catalog(), subject_from(payload))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "missing_historical_projections"


def test_non_historical_projection_does_not_qualify():
    payload = load_json("valid_points.json")
    payload["historical_projections"][0]["is_historical"] = False
    result = evaluate(catalog(), subject_from(payload))
    assert result.reason == "missing_historical_projections"


def test_history_for_another_market_does_not_qualify():
    payload = load_json("valid_points.json")
    payload["historical_projections"][0]["market"] = "rebounds"
    payload["historical_projections"][0]["canonical_projection_id"] = "projection:synthetic-rebounds-only"
    result = evaluate(catalog(), subject_from(payload))
    assert result.reason == "missing_historical_projections"


def test_history_not_required_skips_the_gate():
    subject = load_subject("valid_points.json").model_copy(
        update={"historical_required": False, "historical_projections": ()}
    )
    result = evaluate(catalog(), subject)
    assert result.state is AnalysisState.AVAILABLE
    firing = next(item for item in result.audit.firings if item.rule_id == "missing_historical_projections")
    assert firing.matched is False
