from datetime import datetime, timedelta, timezone

from contracts.common import AnalysisState
from contracts.evidence import UnderperformanceEvidence
from rules.engine import evaluate
from support import catalog, load_subject

AS_OF = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)


def _miss(days_before: int, *, today: bool = False, after_tip: bool = False, missed: bool = True):
    tip = AS_OF - timedelta(days=days_before)
    available = tip + timedelta(hours=1) if after_tip else tip - timedelta(hours=2)
    return {
        "canonical_game_id": "game:synthetic-2026-11-01-home-away",
        "canonical_projection_id": f"projection:synthetic-miss-{days_before}",
        "projection_available_at": available.isoformat().replace("+00:00", "Z"),
        "game_tip_at": tip.isoformat().replace("+00:00", "Z"),
        "is_today_projection": today,
        "missed": missed,
        "snapshot_id": f"snapshot:synthetic-miss-{days_before}",
    }


def _evidence(misses, **updates):
    payload = {
        "misses": misses,
        "stability": {"minutes": True, "usage": True, "shot_volume": True, "snapshot_id": "snapshot:synthetic-stability"},
        "injury": "excluded",
        "illness": "excluded",
        "rest": "excluded",
        "reduced_minutes": "excluded",
        "blowouts": "excluded",
        "declining_production_drivers": "excluded",
        "snapshot_id": "snapshot:synthetic-rebound-1",
        "available_at": "2026-11-01T22:00:00Z",
    }
    payload.update(updates)
    return UnderperformanceEvidence.model_validate(payload)


def _subject(evidence):
    return load_subject("valid_points.json").model_copy(update={"underperformance": evidence})


def _angle(result):
    return next(item for item in result.audit.angles if item.policy_id == "underperformance_rebound")


def test_three_contemporaneous_misses_qualify_without_a_magnitude():
    evidence = _evidence([_miss(6), _miss(4), _miss(2)])
    result = evaluate(catalog(), _subject(evidence))
    angle = _angle(result)
    assert result.state is AnalysisState.AVAILABLE
    assert angle.status == "qualified"
    assert angle.reason == "rebound_facts_verified_magnitude_unset"
    assert angle.adjustment_amount is None
    assert angle.validation_status == "disabled"
    assert "snapshot:synthetic-miss-2" in angle.evidence_snapshot_ids


def test_fewer_than_three_misses_skips_the_angle():
    result = evaluate(catalog(), _subject(_evidence([_miss(6), _miss(4)])))
    assert result.state is AnalysisState.AVAILABLE
    assert _angle(result).status == "skipped"
    assert _angle(result).reason == "insufficient_consecutive_misses"


def test_today_projection_is_not_substituted_for_historical_ones():
    result = evaluate(catalog(), _subject(_evidence([_miss(6), _miss(4), _miss(2, today=True)])))
    assert _angle(result).status == "skipped"
    assert _angle(result).reason == "substituted_current_projection"


def test_a_projection_published_after_tip_does_not_count():
    result = evaluate(catalog(), _subject(_evidence([_miss(6), _miss(4), _miss(2, after_tip=True)])))
    assert _angle(result).status == "skipped"
    assert _angle(result).reason == "insufficient_consecutive_misses"


def test_uncertain_or_missing_causes_skip_the_angle():
    uncertain = evaluate(catalog(), _subject(_evidence([_miss(6), _miss(4), _miss(2)], injury="uncertain")))
    assert _angle(uncertain).status == "skipped"
    assert _angle(uncertain).reason == "uncertain_cause"
    missing = evaluate(catalog(), _subject(_evidence([_miss(6), _miss(4), _miss(2)], illness="missing")))
    assert _angle(missing).reason == "missing_exclusion_evidence"
    assert "illness" in _angle(missing).disclosed_gaps


def test_blowout_or_unstable_volume_excludes_the_angle():
    blowout = evaluate(catalog(), _subject(_evidence([_miss(6), _miss(4), _miss(2)], blowouts="present")))
    assert _angle(blowout).status == "skipped"
    assert _angle(blowout).reason == "exclusion_cause_present"
    unstable = _evidence([_miss(6), _miss(4), _miss(2)])
    stability = unstable.stability.model_copy(update={"minutes": False})
    result = evaluate(catalog(), _subject(unstable.model_copy(update={"stability": stability})))
    assert _angle(result).reason == "unstable_volume"


def test_minutes_change_overrides_a_qualified_rebound():
    subject = _subject(_evidence([_miss(6), _miss(4), _miss(2)]))
    minutes = subject.minutes.model_copy(update={"changed": True})
    result = evaluate(catalog(), subject.model_copy(update={"minutes": minutes}))
    assert result.state is AnalysisState.AVAILABLE
    assert _angle(result).status == "overridden"
