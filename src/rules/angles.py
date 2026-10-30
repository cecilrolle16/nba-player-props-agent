"""Custom angle policies. These do not abstain on the whole analysis."""

from contracts.analysis import AnalysisSubject, AngleAudit
from contracts.common import InjuryStatus, Market
from contracts.evidence import CauseState, EvidenceMark, UnderperformanceEvidence
from rules.catalog import RuleCatalog

POLICY_IDS = ("defender_return", "no_streak_chasing", "underperformance_rebound")
LIMITING_INJURY = {InjuryStatus.OUT, InjuryStatus.DOUBTFUL, InjuryStatus.QUESTIONABLE}
CLEAR_RESTRICTIONS = {"restricted", "none"}
CLEAR_ROTATION = {"present"}


def _ids(*values: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def override_reasons(subject: AnalysisSubject) -> tuple[str, ...]:
    found: list[str] = []
    if subject.injury is not None and subject.injury.status in LIMITING_INJURY:
        found.append("injury_news")
    if subject.availability is not None and subject.availability.injury_news:
        found.append("injury_news")
    if subject.minutes is not None and subject.minutes.changed:
        found.append("minutes_change")
    if subject.minutes is not None and subject.minutes.restricted:
        found.append("minutes_restriction")
    if subject.role is not None and subject.role.changed:
        found.append("role_change")
    return tuple(dict.fromkeys(found))


def essential_gaps(subject: AnalysisSubject) -> tuple[str, ...]:
    gaps: list[str] = []
    availability = subject.availability
    if availability is None or not availability.status_known or availability.snapshot_id is None:
        gaps.append("availability")
    minutes = subject.minutes
    if (
        minutes is None
        or not minutes.known
        or minutes.snapshot_id is None
        or minutes.expected_minutes is None
    ):
        gaps.append("minutes")
    return tuple(gaps)


def subject_disclosures(subject: AnalysisSubject) -> tuple[str, ...]:
    if subject.role is None or subject.role.snapshot_id is None:
        return ("role_evidence_missing",)
    return ()


def _audit(
    policy_id: str,
    status: str,
    reason: str,
    snapshots: tuple[str, ...],
    gaps: tuple[str, ...] = (),
    validation_status: str = "disabled",
) -> AngleAudit:
    return AngleAudit(
        policy_id=policy_id,
        status=status,
        reason=reason,
        evidence_snapshot_ids=snapshots,
        adjustment_amount=None,
        validation_status=validation_status,  # type: ignore[arg-type]
        disclosed_gaps=gaps,
    )


def _overridden(policy_id: str, snapshots: tuple[str, ...], reasons: tuple[str, ...]) -> AngleAudit:
    return _audit(policy_id, "overridden", "injury_minutes_or_role_override", snapshots, reasons)


def _defender_snapshots(subject: AnalysisSubject) -> tuple[str, ...]:
    evidence = subject.defender_return
    if evidence is None:
        return ()
    return _ids(
        evidence.return_date_snapshot_id,
        evidence.minutes_snapshot_id,
        evidence.key_defender_snapshot_id,
        evidence.defensive_rotation_snapshot_id,
    )


def _defender_gaps(subject: AnalysisSubject) -> tuple[str, ...]:
    evidence = subject.defender_return
    gaps: list[str] = []
    if evidence is None or evidence.confirmed_return_date is None:
        gaps.append("confirmed_return_date")
    if evidence is None or evidence.games_since_return is None:
        gaps.append("games_since_return")
    if evidence is None or evidence.minutes_since_return is None:
        gaps.append("minutes_since_return")
    if evidence is None or evidence.opponent_is_key_defender is None:
        gaps.append("key_defensive_player")
    return tuple(gaps)


def _secondary_unclear(subject: AnalysisSubject) -> tuple[str, ...]:
    evidence = subject.defender_return
    gaps: list[str] = []
    if evidence is None or evidence.minutes_restriction not in CLEAR_RESTRICTIONS:
        gaps.append("minutes_restriction")
    if evidence is None or evidence.defensive_rotation_support not in CLEAR_ROTATION:
        gaps.append("defensive_rotation_evidence")
    return tuple(gaps)


def _evaluate_defender(catalog: RuleCatalog, subject: AnalysisSubject) -> AngleAudit:
    policy = catalog.angle_policies.defender_return
    snapshots = _defender_snapshots(subject)
    if subject.market not in policy.markets:
        return _audit("defender_return", "not_applicable", "defender_return_not_applicable", snapshots)
    overrides = override_reasons(subject)
    if overrides:
        return _overridden("defender_return", snapshots, overrides)
    gaps = _defender_gaps(subject)
    if gaps:
        return _audit("defender_return", "skipped", "missing_required_return_evidence", snapshots, gaps)
    evidence = subject.defender_return
    assert evidence is not None
    if evidence.opponent_is_key_defender is False:
        return _audit("defender_return", "skipped", "opponent_not_key_defender", snapshots)
    if evidence.normal_minutes_supported is True and evidence.healthy_defensive_performance_supported is True:
        return _audit("defender_return", "reduced", "normal_minutes_and_healthy_defense", snapshots)
    secondary = _secondary_unclear(subject)
    if secondary:
        if policy.flagged_research_when_secondary_unclear:
            return _audit(
                "defender_return",
                "flagged_research",
                "secondary_evidence_unclear",
                snapshots,
                secondary,
                "unvalidated",
            )
        return _audit("defender_return", "skipped", "unclear_secondary_evidence", snapshots, secondary)
    return _audit("defender_return", "qualified", "return_facts_verified_magnitude_unset", snapshots)


def _streak_marks(subject: AnalysisSubject, names: tuple[str, ...]) -> dict[str, EvidenceMark]:
    evidence = subject.streak
    correlated = set(evidence.correlated_indicators) if evidence is not None else set()
    marks: dict[str, EvidenceMark] = {}
    for name in names:
        raw = getattr(evidence, name) if evidence is not None else EvidenceMark.ABSENT
        if name in correlated or raw is EvidenceMark.CORRELATED:
            marks[name] = EvidenceMark.CORRELATED
        elif raw is EvidenceMark.INDEPENDENT:
            marks[name] = EvidenceMark.INDEPENDENT
        else:
            marks[name] = EvidenceMark.ABSENT
    return marks


def _evaluate_streak(catalog: RuleCatalog, subject: AnalysisSubject) -> AngleAudit:
    policy = catalog.angle_policies.no_streak_chasing
    evidence = subject.streak
    snapshots = _ids(None if evidence is None else evidence.snapshot_id)
    if subject.market not in policy.markets:
        return _audit("no_streak_chasing", "not_applicable", "market_out_of_scope", snapshots)
    overrides = override_reasons(subject)
    if overrides:
        return _overridden("no_streak_chasing", snapshots, overrides)
    marks = _streak_marks(subject, policy.independent_evidence)
    absent = tuple(name for name, mark in marks.items() if mark is EvidenceMark.ABSENT)
    independent = [name for name, mark in marks.items() if mark is EvidenceMark.INDEPENDENT]
    successes = 0 if evidence is None else evidence.consecutive_successes
    if successes <= 0:
        return _audit("no_streak_chasing", "not_triggered", "no_streak", snapshots, absent)
    if not independent:
        return _audit(
            "no_streak_chasing",
            "unsupported",
            "streak_alone_does_not_raise_projection",
            snapshots,
            absent,
        )
    return _audit(
        "no_streak_chasing",
        "disclosed",
        "independent_evidence_recorded_no_confidence_boost",
        snapshots,
        absent,
    )


def _cause(evidence: UnderperformanceEvidence | None, name: str) -> CauseState:
    if evidence is None:
        return CauseState.MISSING
    return getattr(evidence, name)


def _contemporaneous(miss, as_of) -> bool:
    return (
        miss.missed
        and not miss.is_today_projection
        and miss.projection_available_at <= miss.game_tip_at
        and miss.projection_available_at <= as_of
    )


def _longest_run(evidence: UnderperformanceEvidence | None, as_of) -> int:
    if evidence is None:
        return 0
    run = best = 0
    for miss in evidence.misses:
        if _contemporaneous(miss, as_of):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _evaluate_rebound(catalog: RuleCatalog, subject: AnalysisSubject) -> AngleAudit:
    policy = catalog.angle_policies.underperformance_rebound
    evidence = subject.underperformance
    snapshots = _ids(None if evidence is None else evidence.snapshot_id)
    if evidence is not None:
        snapshots = _ids(
            evidence.snapshot_id,
            None if evidence.stability is None else evidence.stability.snapshot_id,
            *[miss.snapshot_id for miss in evidence.misses],
        )
    if subject.market not in policy.markets:
        return _audit("underperformance_rebound", "not_applicable", "market_out_of_scope", snapshots)
    overrides = override_reasons(subject)
    if overrides:
        return _overridden("underperformance_rebound", snapshots, overrides)
    if evidence is not None and any(miss.is_today_projection for miss in evidence.misses):
        return _audit(
            "underperformance_rebound",
            "skipped",
            "substituted_current_projection",
            snapshots,
        )
    if _longest_run(evidence, subject.as_of) < policy.minimum_consecutive_misses:
        return _audit(
            "underperformance_rebound",
            "skipped",
            "insufficient_consecutive_misses",
            snapshots,
        )
    stability = None if evidence is None else evidence.stability
    unstable = [
        name
        for name in policy.required_stability
        if stability is None or getattr(stability, name) is not True
    ]
    if unstable:
        reason = "missing_stability_evidence" if any(
            stability is None or getattr(stability, name) is None for name in unstable
        ) else "unstable_volume"
        return _audit("underperformance_rebound", "skipped", reason, snapshots, tuple(unstable))
    uncertain = tuple(
        name for name in policy.exclusion_causes if _cause(evidence, name) is CauseState.UNCERTAIN
    )
    missing = tuple(
        name for name in policy.exclusion_causes if _cause(evidence, name) is CauseState.MISSING
    )
    present = tuple(
        name for name in policy.exclusion_causes if _cause(evidence, name) is CauseState.PRESENT
    )
    if uncertain:
        return _audit("underperformance_rebound", "skipped", "uncertain_cause", snapshots, uncertain)
    if missing:
        return _audit(
            "underperformance_rebound",
            "skipped",
            "missing_exclusion_evidence",
            snapshots,
            missing,
        )
    if present:
        return _audit(
            "underperformance_rebound",
            "skipped",
            "exclusion_cause_present",
            snapshots,
            present,
        )
    return _audit(
        "underperformance_rebound",
        "qualified",
        "rebound_facts_verified_magnitude_unset",
        snapshots,
    )


def evaluate_angles(catalog: RuleCatalog, subject: AnalysisSubject) -> tuple[AngleAudit, ...]:
    audits = (
        _evaluate_defender(catalog, subject),
        _evaluate_streak(catalog, subject),
        _evaluate_rebound(catalog, subject),
    )
    return tuple(audits)
