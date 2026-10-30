"""Evaluate versioned rules against one pregame subject."""

from datetime import timedelta
from typing import NamedTuple

from contracts.analysis import (
    AdjustmentDecision,
    AnalysisResult,
    AnalysisSubject,
    FutureContextNote,
    RequestedAdjustment,
    RuleFiring,
)
from contracts.capabilities import classify_capability
from contracts.common import (
    BLOCKING_EFFECTS,
    GAME_ID,
    KNOWN_CAPABILITIES,
    PLAYER_ID,
    AnalysisState,
)
from contracts.market import quote_problems
from contracts.projection import Projection
from rules.angles import essential_gaps, evaluate_angles, subject_disclosures
from rules.audit import make_audit
from rules.catalog import FUTURE_CONTEXT_MODELS, RuleCatalog, RuleSpec


class Match(NamedTuple):
    matched: bool
    effect: str
    reason: str


def _player_ok(value: str | None) -> bool:
    return value is not None and PLAYER_ID.fullmatch(value) is not None


def _eval_unknown(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    if subject.capability is None:
        return Match(False, rule.effect, rule.reason)
    decision = classify_capability(subject.capability)
    if decision.reason == "unknown_provider_capability":
        return Match(True, AnalysisState.UNAVAILABLE.value, decision.reason)
    return Match(False, rule.effect, rule.reason)


def _eval_unsupported(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    if subject.capability is None or subject.declared_capabilities is None:
        return Match(False, rule.effect, rule.reason)
    if subject.capability not in KNOWN_CAPABILITIES:
        return Match(False, rule.effect, rule.reason)
    if subject.capability not in subject.declared_capabilities:
        return Match(True, AnalysisState.UNAVAILABLE.value, "unsupported_capability")
    return Match(False, rule.effect, rule.reason)


def _moment_is_late(moment, as_of) -> bool:
    return moment is not None and moment > as_of


def _evidence_is_late(subject: AnalysisSubject) -> bool:
    records = (
        subject.availability,
        subject.minutes,
        subject.role,
        subject.defender_return,
        subject.streak,
        subject.underperformance,
    )
    if any(_moment_is_late(getattr(record, "available_at", None), subject.as_of) for record in records):
        return True
    if subject.underperformance is not None:
        for miss in subject.underperformance.misses:
            if _moment_is_late(miss.projection_available_at, subject.as_of):
                return True
    return False


def _eval_not_yet(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    late = (
        subject.snapshot.available_at > subject.as_of
        or subject.snapshot.captured_at > subject.as_of
        or _evidence_is_late(subject)
    )
    injury = subject.injury
    if injury is not None and (injury.available_at > subject.as_of or injury.reported_at > subject.as_of):
        late = True
    for projection in subject.historical_projections:
        if projection.available_at > subject.as_of or projection.projected_at > subject.as_of:
            late = True
    if late:
        return Match(True, AnalysisState.UNAVAILABLE.value, "not_yet_available")
    return Match(False, rule.effect, rule.reason)


def _eval_malformed(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    if quote_problems(subject.snapshot, subject.market.value):
        return Match(True, AnalysisState.UNAVAILABLE.value, "malformed_odds")
    return Match(False, rule.effect, rule.reason)


def _eval_unresolved(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    quote_id = subject.snapshot.canonical_player_id
    subject_ok = _player_ok(subject.canonical_player_id)
    quote_ok = _player_ok(quote_id)
    injury = subject.injury
    injury_mismatch = injury is not None and subject_ok and injury.canonical_player_id != subject.canonical_player_id
    if not subject_ok or not quote_ok:
        return Match(True, AnalysisState.UNAVAILABLE.value, "unresolved_player_id")
    if subject.canonical_player_id != quote_id or injury_mismatch:
        return Match(True, AnalysisState.UNAVAILABLE.value, "player_id_mismatch")
    quote_game = subject.snapshot.canonical_game_id
    if (
        subject.canonical_game_id is not None
        and quote_game is not None
        and GAME_ID.fullmatch(subject.canonical_game_id) is not None
        and GAME_ID.fullmatch(quote_game) is not None
        and subject.canonical_game_id != quote_game
    ):
        return Match(True, AnalysisState.UNAVAILABLE.value, "game_id_mismatch")
    return Match(False, rule.effect, rule.reason)


def _eval_stale(catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    injury = subject.injury
    if injury is None or injury.available_at > subject.as_of:
        return Match(False, rule.effect, rule.reason)
    if subject.canonical_player_id is not None and injury.canonical_player_id != subject.canonical_player_id:
        return Match(False, rule.effect, rule.reason)
    limit = timedelta(minutes=catalog.injury_freshness.max_age_minutes)
    if subject.as_of - injury.available_at > limit:
        return Match(True, AnalysisState.SKIP.value, "stale_injury_report")
    return Match(False, rule.effect, rule.reason)


def _qualifying(subject: AnalysisSubject, projection: Projection) -> bool:
    return (
        projection.is_historical
        and projection.market == subject.market
        and projection.canonical_player_id == subject.canonical_player_id
        and projection.canonical_game_id == subject.canonical_game_id
        and projection.available_at <= subject.as_of
        and projection.projected_at <= subject.as_of
    )


def _eval_history(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    if not subject.historical_required:
        return Match(False, rule.effect, rule.reason)
    found = any(_qualifying(subject, projection) for projection in subject.historical_projections)
    if not found:
        return Match(True, AnalysisState.UNAVAILABLE.value, "missing_historical_projections")
    return Match(False, rule.effect, rule.reason)


def _eval_essential(_catalog: RuleCatalog, rule: RuleSpec, subject: AnalysisSubject) -> Match:
    if essential_gaps(subject):
        return Match(True, AnalysisState.UNAVAILABLE.value, "missing_essential_evidence")
    return Match(False, rule.effect, rule.reason)


EVALUATORS = {
    "unknown_provider_capability": _eval_unknown,
    "unsupported_capability": _eval_unsupported,
    "not_yet_available": _eval_not_yet,
    "malformed_odds": _eval_malformed,
    "unresolved_player_id": _eval_unresolved,
    "stale_injury_report": _eval_stale,
    "missing_essential_evidence": _eval_essential,
    "missing_historical_projections": _eval_history,
}


def _adjustment_reason(catalog: RuleCatalog, requested: RequestedAdjustment) -> str:
    if requested.adjustment_id in FUTURE_CONTEXT_MODELS:
        return "future_context_model"
    if requested.adjustment_id in catalog.hypothesis_magnitudes:
        return "angle_magnitude_unset"
    return "unknown_adjustment"


def decide_adjustments(catalog: RuleCatalog, subject: AnalysisSubject) -> tuple[AdjustmentDecision, ...]:
    return tuple(
        AdjustmentDecision(
            adjustment_id=item.adjustment_id,
            market=subject.market,
            applied=False,
            delta=None,
            reason=_adjustment_reason(catalog, item),
        )
        for item in subject.requested_adjustments
    )


def future_context_notes(subject: AnalysisSubject) -> tuple[FutureContextNote, ...]:
    return tuple(
        FutureContextNote(model_id=item.adjustment_id)
        for item in subject.requested_adjustments
        if item.adjustment_id in FUTURE_CONTEXT_MODELS
    )


def evaluate(catalog: RuleCatalog, subject: AnalysisSubject) -> AnalysisResult:
    firings: list[RuleFiring] = []
    for rule in catalog.ordered():
        evaluator = EVALUATORS.get(rule.id)
        if evaluator is None:
            match = Match(True, AnalysisState.UNAVAILABLE.value, "unknown_rule")
        else:
            match = evaluator(catalog, rule, subject)
        firings.append(
            RuleFiring(
                rule_id=rule.id,
                precedence=rule.precedence,
                matched=match.matched,
                effect=match.effect,
                reason=match.reason,
            )
        )
    blocking = [item for item in firings if item.matched and item.effect in BLOCKING_EFFECTS]
    if blocking:
        state = AnalysisState(blocking[0].effect)
        reason = blocking[0].reason
    else:
        state = AnalysisState.AVAILABLE
        reason = "inputs_accepted"
    adjustments = decide_adjustments(catalog, subject)
    if state is AnalysisState.AVAILABLE and any(item.reason == "unknown_adjustment" for item in adjustments):
        state = AnalysisState.UNAVAILABLE
        reason = "unknown_adjustment"
    if state is AnalysisState.AVAILABLE and catalog.numeric_hypothesis_adjustments_enabled:
        state = AnalysisState.UNAVAILABLE
        reason = "adjustments_not_permitted"
    angles = evaluate_angles(catalog, subject)
    gaps = essential_gaps(subject)
    disclosures = subject_disclosures(subject)
    context = future_context_notes(subject)
    audit = make_audit(catalog, subject, state, reason, tuple(firings), angles, adjustments)
    return AnalysisResult(
        state=state,
        reason=reason,
        canonical_player_id=subject.canonical_player_id,
        canonical_game_id=subject.canonical_game_id,
        market=subject.market,
        snapshot_id=subject.snapshot.snapshot_id,
        model_version=catalog.model_version,
        rules_version=catalog.rules_version,
        available_at=subject.as_of,
        essential_gaps=gaps,
        disclosed_gaps=disclosures,
        adjustments=adjustments,
        future_context=context,
        audit=audit,
    )
