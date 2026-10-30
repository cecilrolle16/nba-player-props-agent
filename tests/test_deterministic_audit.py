from datetime import timedelta

from rules.engine import evaluate
from support import catalog, load_subject


def test_same_input_produces_the_same_audit():
    rules = catalog()
    subject = load_subject("valid_points.json")
    first = evaluate(rules, subject)
    second = evaluate(rules, subject)
    assert first.audit.audit_id == second.audit.audit_id
    assert len(first.audit.audit_id) == 64
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.available_at == subject.as_of
    assert first.audit.rules_version == rules.rules_version
    assert first.audit.model_version == "none"
    assert first.audit.rules_fingerprint == rules.source_sha256


def test_audit_id_changes_when_the_decision_time_changes():
    rules = catalog()
    subject = load_subject("valid_points.json")
    moved = subject.model_copy(update={"as_of": subject.as_of + timedelta(seconds=1)})
    assert evaluate(rules, subject).audit.audit_id != evaluate(rules, moved).audit.audit_id


def test_audit_preserves_firing_order():
    result = evaluate(catalog(), load_subject("stale_injury.json"))
    precedences = [item.precedence for item in result.audit.firings]
    assert precedences == sorted(precedences)
