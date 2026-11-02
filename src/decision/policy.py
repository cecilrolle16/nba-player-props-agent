"""Apply an explicit research policy. This does not stake or recommend a bet.

The shipped configuration leaves both minima and the edge benchmark empty.
A missing no-vig price is not replaced with the raw implied probability.
All-push prices are no_bet. Missing, stale, or unresolved inputs are unavailable.
"""

from contracts.analysis import AnalysisResult
from contracts.common import AnalysisState
from contracts.comparison import MarketComparison
from contracts.configuration import ResearchDecisionSettings
from contracts.distribution import ProjectionDistribution
from contracts.price import PriceComparison
from contracts.research import GATE_IDS, POLICY_VERSION, REQUIRED_ANGLE_IDS, DecisionGate, ResearchDecision
from contracts.stages import CalibrationStatus, StageState, ValueOrigin

DEFAULT_RESEARCH_POLICY = ResearchDecisionSettings(
    version=1,
    minimum_ev=None,
    minimum_probability_edge=None,
    edge_benchmark=None,
    configuration_label="unset",
    thresholds_validated=False,
    recommendations_enabled=False,
    hypothesis_adjustments_enabled=False,
)


def research_decision(
    *,
    validation: AnalysisResult,
    comparison: MarketComparison,
    projection: ProjectionDistribution,
    price: PriceComparison | None,
    policy: ResearchDecisionSettings | None = None,
) -> ResearchDecision:
    """Record every gate. Edge and EV remain on the price record."""

    chosen = policy or DEFAULT_RESEARCH_POLICY
    angles = tuple(item.policy_id for item in validation.audit.angles)
    rules_ok = angles == REQUIRED_ANGLE_IDS and all(item.adjustment_amount is None for item in validation.audit.angles)
    adjustments_ok = rules_ok and all(item.delta is None and item.applied is False for item in validation.adjustments)
    eligible = validation.state is AnalysisState.AVAILABLE
    price_ok, price_reason = _price_gate(comparison, price)
    thresholds_ok = chosen.minimum_ev is not None and chosen.minimum_probability_edge is not None
    benchmark_ok = chosen.edge_benchmark is not None
    all_push = price is not None and price.status == "available" and price.edge_reason == "all_push"
    benchmark_match, benchmark_reason = _benchmark_gate(chosen, price, all_push, price_ok and thresholds_ok and benchmark_ok)
    numbers = price_ok and thresholds_ok and benchmark_ok and benchmark_match is True and not all_push and price is not None
    if all_push and price_ok:
        ev_ok, ev_reason = False, "nonpositive_ev"
        edge_ok, edge_reason = None, "not_checked"
    else:
        ev_ok, ev_reason = _positive(numbers, None if price is None else price.ev_per_unit, "ev")
        edge_ok, edge_reason = _positive(numbers, None if price is None else price.probability_edge, "edge")
    minimum_ev_ok, minimum_ev_reason = _minimum(
        numbers and ev_ok, None if price is None else price.ev_per_unit, chosen.minimum_ev, "ev"
    )
    minimum_edge_ok, minimum_edge_reason = _minimum(
        numbers and edge_ok,
        None if price is None else price.probability_edge,
        chosen.minimum_probability_edge,
        "edge",
    )
    research_ok = price_ok and price is not None and price.calibration_status is CalibrationStatus.UNCALIBRATED
    gates = (
        _gate("inputs_eligible", eligible, "inputs_accepted" if eligible else validation.reason),
        _gate("custom_rules_audited", rules_ok, "three_custom_rules" if rules_ok else "custom_rules_incomplete"),
        _gate("adjustments_disabled", adjustments_ok, "numeric_adjustments_disabled" if adjustments_ok else "numeric_adjustment"),
        _gate("price_available", price_ok, "price_bound" if price_ok else price_reason),
        _gate("thresholds_configured", thresholds_ok, "thresholds_present" if thresholds_ok else "missing_thresholds"),
        _gate("benchmark_configured", benchmark_ok, "benchmark_present" if benchmark_ok else "missing_edge_benchmark"),
        _gate("not_all_push", *_push_gate(price_ok, all_push)),
        _gate("benchmark_matched", benchmark_match, benchmark_reason),
        _gate("ev_positive", ev_ok, ev_reason),
        _gate("edge_positive", edge_ok, edge_reason),
        _gate("minimum_ev", minimum_ev_ok, minimum_ev_reason),
        _gate("minimum_probability_edge", minimum_edge_ok, minimum_edge_reason),
        _gate("research_label", *_research_gate(price_ok, research_ok)),
    )
    return ResearchDecision(
        decision_id=f"decision:research-{comparison.market.value}",
        policy_version=POLICY_VERSION,
        result=_result(gates),
        data_quality=validation.state,
        calibration_status=price.calibration_status if price is not None else CalibrationStatus.NOT_APPLICABLE,
        hit_probability_reference=None if price is None else price.price_id,
        origin="synthetic" if _synthetic(comparison) else "observed",
        experimental=True,
        thresholds_validated=False,
        configuration_label=chosen.configuration_label,
        minimum_ev=chosen.minimum_ev,
        minimum_probability_edge=chosen.minimum_probability_edge,
        edge_benchmark=chosen.edge_benchmark,
        priced_edge_benchmark=None if price is None else price.edge_benchmark,
        comparison_id=comparison.comparison_id,
        canonical_projection_id=projection.canonical_projection_id,
        projection_stage=projection.stage_state,
        distribution_mean_resolved=price is not None and price.distribution_mean is not None,
        price_id=None if price is None else price.price_id,
        odds_snapshot_id=comparison.odds_snapshot_id,
        audit_id=validation.audit.audit_id,
        angle_policy_ids=REQUIRED_ANGLE_IDS if rules_ok else angles,
        rules_version=validation.rules_version,
        gates=gates,
    )


def _price_gate(comparison: MarketComparison, price: PriceComparison | None) -> tuple[bool, str]:
    if price is None:
        return False, "missing_price"
    if price.status != "available":
        return False, price.unavailable_reason or "price_unavailable"
    if (
        price.comparison_id != comparison.comparison_id
        or price.canonical_player_id != comparison.canonical_player_id
        or price.canonical_game_id != comparison.canonical_game_id
        or price.market is not comparison.market
        or price.line != comparison.line
        or price.side != comparison.side
        or price.odds_snapshot_id != comparison.odds_snapshot_id
    ):
        return False, "identity_mismatch"
    if comparison.book is not None and price.book != comparison.book:
        return False, "identity_mismatch"
    if comparison.american_odds is not None and price.american_odds != comparison.american_odds:
        return False, "identity_mismatch"
    return True, "price_bound"


def _benchmark_gate(
    policy: ResearchDecisionSettings,
    price: PriceComparison | None,
    all_push: bool,
    ready: bool,
) -> tuple[bool | None, str]:
    if not ready or all_push or price is None:
        return None, "not_checked"
    if price.edge_benchmark != policy.edge_benchmark:
        if policy.edge_benchmark == "no_vig":
            return False, "required_no_vig_unavailable"
        return False, "edge_benchmark_mismatch"
    return True, policy.edge_benchmark or "benchmark_matched"


def _positive(ready: bool, value: float | None, name: str) -> tuple[bool | None, str]:
    if not ready:
        return None, "not_checked"
    if value is None:
        return False, f"missing_{name}"
    if value <= 0:
        return False, f"nonpositive_{name}"
    return True, f"positive_{name}"


def _minimum(ready: bool, value: float | None, minimum: float | None, name: str) -> tuple[bool | None, str]:
    if not ready or value is None or minimum is None:
        return None, "not_checked"
    if value < minimum:
        return False, f"{name}_below_minimum"
    return True, f"{name}_clears_minimum"


def _push_gate(price_ok: bool, all_push: bool) -> tuple[bool | None, str]:
    if not price_ok:
        return None, "not_checked"
    if all_push:
        return False, "all_push"
    return True, "priced_side"


def _research_gate(price_ok: bool, research_ok: bool) -> tuple[bool | None, str]:
    if not price_ok:
        return None, "not_checked"
    if research_ok:
        return True, "uncalibrated_research_only"
    return False, "not_a_research_price"


def _gate(gate_id: str, passed: bool | None, reason: str) -> DecisionGate:
    if passed is None:
        status = "not_checked"
    elif passed:
        status = "passed"
    else:
        status = "failed"
    return DecisionGate(gate_id=gate_id, status=status, reason=reason)


def _result(gates: tuple[DecisionGate, ...]) -> str:
    by_id = {gate.gate_id: gate.status for gate in gates}
    if tuple(by_id) != GATE_IDS:
        return "unavailable"
    if any(by_id[gate_id] == "failed" for gate_id in (
        "inputs_eligible",
        "custom_rules_audited",
        "adjustments_disabled",
        "price_available",
        "thresholds_configured",
        "benchmark_configured",
        "benchmark_matched",
        "research_label",
    )):
        return "unavailable"
    if any(by_id[gate_id] == "failed" for gate_id in (
        "not_all_push",
        "ev_positive",
        "edge_positive",
        "minimum_ev",
        "minimum_probability_edge",
    )):
        return "no_bet"
    if any(status != "passed" for status in by_id.values()):
        return "unavailable"
    return "research_candidate"


def _synthetic(comparison: MarketComparison) -> bool:
    return comparison.origin is ValueOrigin.SYNTHETIC or comparison.stage_state is StageState.SYNTHETIC
