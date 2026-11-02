"""Research-only decision. It is not a stake and not a live recommendation.

Edge and EV stay on the price record. This record stores the gate results
and the ids of the records it used. `MarketComparison.implied_probability`
stays null; the calculated implied probability is `PriceComparison.raw_implied_probability`.
"""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import (
    AnalysisState,
    ComparisonId,
    DecisionId,
    PriceId,
    ProjectionId,
    SnapshotId,
    StrictModel,
)
from contracts.stages import CalibrationStatus, StageState

POLICY_VERSION = "2026-11-01.research-decision"
REQUIRED_ANGLE_IDS = (
    "defender_return",
    "no_streak_chasing",
    "underperformance_rebound",
)
GATE_IDS = (
    "inputs_eligible",
    "custom_rules_audited",
    "adjustments_disabled",
    "price_available",
    "thresholds_configured",
    "benchmark_configured",
    "not_all_push",
    "benchmark_matched",
    "ev_positive",
    "edge_positive",
    "minimum_ev",
    "minimum_probability_edge",
    "research_label",
)
FAIL_CLOSED_GATES = frozenset(
    {
        "inputs_eligible",
        "custom_rules_audited",
        "adjustments_disabled",
        "price_available",
        "thresholds_configured",
        "benchmark_configured",
        "benchmark_matched",
        "research_label",
    }
)
NO_BET_GATES = frozenset(
    {
        "not_all_push",
        "ev_positive",
        "edge_positive",
        "minimum_ev",
        "minimum_probability_edge",
    }
)


class DecisionGate(StrictModel):
    gate_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "not_checked"]
    reason: str = Field(min_length=1)


class ResearchDecision(StrictModel):
    decision_id: DecisionId
    policy_version: Literal["2026-11-01.research-decision"]
    result: Literal["research_candidate", "no_bet", "unavailable"]
    data_quality: AnalysisState
    calibration_status: CalibrationStatus
    hit_probability_reference: PriceId | None = None
    origin: Literal["observed", "synthetic"]
    experimental: Literal[True]
    live_recommendation: Literal[False] = False
    actionable: Literal[False] = False
    stake: None = None
    bankroll_sizing: None = None
    thresholds_validated: Literal[False]
    recommendations_enabled: Literal[False] = False
    hypothesis_adjustments_enabled: Literal[False] = False
    configuration_label: Literal["unset", "synthetic_test"]
    minimum_ev: float | None = Field(default=None, allow_inf_nan=False)
    minimum_probability_edge: float | None = Field(default=None, allow_inf_nan=False)
    edge_benchmark: Literal["raw_implied", "no_vig"] | None = None
    priced_edge_benchmark: Literal["raw_implied", "no_vig"] | None = None
    comparison_id: ComparisonId
    canonical_projection_id: ProjectionId
    projection_stage: StageState
    distribution_mean_resolved: bool
    price_id: PriceId | None = None
    odds_snapshot_id: SnapshotId
    audit_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    angle_policy_ids: tuple[str, ...]
    rules_version: str = Field(min_length=1)
    gates: tuple[DecisionGate, ...]

    @model_validator(mode="after")
    def result_follows_the_gates(self) -> "ResearchDecision":
        if tuple(gate.gate_id for gate in self.gates) != GATE_IDS:
            raise ValueError("research gates are recorded in policy order")
        if self.angle_policy_ids != REQUIRED_ANGLE_IDS and any(
            gate.gate_id == "custom_rules_audited" and gate.status == "passed" for gate in self.gates
        ):
            raise ValueError("a passed rules gate names the three custom policies")
        failed_closed = any(gate.status == "failed" and gate.gate_id in FAIL_CLOSED_GATES for gate in self.gates)
        failed_bet = any(gate.status == "failed" and gate.gate_id in NO_BET_GATES for gate in self.gates)
        if self.result == "unavailable" and not failed_closed:
            raise ValueError("unavailable names a failed closed gate")
        if self.result == "no_bet" and (failed_closed or not failed_bet):
            raise ValueError("no_bet is a priced side that misses a betting gate")
        if self.result == "research_candidate":
            if failed_closed or failed_bet:
                raise ValueError("a research candidate passed every gate")
            if self.data_quality is not AnalysisState.AVAILABLE:
                raise ValueError("data quality stays separate and must be available")
            if self.calibration_status is not CalibrationStatus.UNCALIBRATED:
                raise ValueError("only an uncalibrated price can be a research candidate")
            if self.hit_probability_reference is None or self.price_id is None:
                raise ValueError("a research candidate references the price record")
        if self.configuration_label == "unset" and (
            self.minimum_ev is not None or self.minimum_probability_edge is not None or self.edge_benchmark is not None
        ):
            raise ValueError("an unset policy does not carry a threshold")
        return self
