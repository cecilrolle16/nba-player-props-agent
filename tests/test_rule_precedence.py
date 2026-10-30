import json

from contracts.common import AnalysisState
from rules.engine import evaluate
from support import ROOT, catalog, load_json, load_subject, subject_from


def test_rules_are_applied_by_precedence_not_file_order():
    raw = json.loads((ROOT / "config" / "custom-rules.json").read_text(encoding="utf-8"))
    file_order = [rule["id"] for rule in raw["rules"]]
    precedence_order = [rule.id for rule in catalog().ordered()]
    assert file_order != precedence_order
    assert precedence_order == [
        "unknown_provider_capability",
        "unsupported_capability",
        "not_yet_available",
        "malformed_odds",
        "unresolved_player_id",
        "stale_injury_report",
        "missing_essential_evidence",
        "missing_historical_projections",
    ]


def test_malformed_odds_outrank_unresolved_player():
    payload = load_json("malformed_odds.json")
    payload["canonical_player_id"] = None
    payload["snapshot"]["canonical_player_id"] = None
    result = evaluate(catalog(), subject_from(payload))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "malformed_odds"
    matched = [item.rule_id for item in result.audit.firings if item.matched and item.effect in {"unavailable", "skip"}]
    assert matched[0] == "malformed_odds"
    assert "unresolved_player_id" in matched
    assert [item.rule_id for item in result.audit.firings] == [rule.id for rule in catalog().ordered()]


def test_stale_injury_outranks_missing_history():
    payload = load_json("stale_injury.json")
    payload["historical_projections"] = []
    result = evaluate(catalog(), subject_from(payload))
    assert result.state is AnalysisState.SKIP
    assert result.reason == "stale_injury_report"
    reasons = [item.reason for item in result.audit.firings if item.matched]
    assert reasons.index("stale_injury_report") < reasons.index("missing_historical_projections")


def test_unknown_capability_outranks_malformed_odds():
    subject = load_subject("malformed_odds.json").model_copy(update={"capability": "live_bets"})
    result = evaluate(catalog(), subject)
    assert result.reason == "unknown_provider_capability"
