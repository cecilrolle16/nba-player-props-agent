from datetime import datetime, timedelta, timezone

from contracts.common import AnalysisState
from rules.engine import evaluate
from support import catalog, load_subject

AS_OF = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)


def _with_injury_age(minutes: int):
    subject = load_subject("valid_points.json")
    available = AS_OF - timedelta(minutes=minutes)
    injury = subject.injury.model_copy(update={"reported_at": available, "available_at": available})
    return subject.model_copy(update={"injury": injury})


def test_stale_injury_skips_player():
    result = evaluate(catalog(), load_subject("stale_injury.json"))
    assert result.state is AnalysisState.SKIP
    assert result.reason == "stale_injury_report"
    firing = next(item for item in result.audit.firings if item.rule_id == "stale_injury_report")
    assert firing.matched is True


def test_injury_at_exact_window_is_fresh():
    result = evaluate(catalog(), _with_injury_age(180))
    assert result.state is AnalysisState.AVAILABLE
    assert result.reason == "inputs_accepted"


def test_injury_one_second_past_window_skips():
    subject = load_subject("valid_points.json")
    available = AS_OF - timedelta(minutes=180, seconds=1)
    injury = subject.injury.model_copy(update={"reported_at": available, "available_at": available})
    result = evaluate(catalog(), subject.model_copy(update={"injury": injury}))
    assert result.state is AnalysisState.SKIP
    assert result.reason == "stale_injury_report"


def test_future_injury_is_not_yet_available():
    subject = load_subject("valid_points.json")
    future = AS_OF + timedelta(minutes=30)
    injury = subject.injury.model_copy(update={"reported_at": future, "available_at": future})
    result = evaluate(catalog(), subject.model_copy(update={"injury": injury}))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "not_yet_available"


def test_missing_injury_does_not_skip_when_availability_is_known():
    subject = load_subject("valid_points.json")
    result = evaluate(catalog(), subject.model_copy(update={"injury": None}))
    assert result.state is AnalysisState.AVAILABLE
    assert result.reason == "inputs_accepted"
