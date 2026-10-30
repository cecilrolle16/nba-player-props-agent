"""Inputs and outputs of one pregame rules pass."""

from typing import Literal

from contracts.common import UTCDateTime, AnalysisState, Market, StrictModel
from contracts.evidence import (
    AvailabilityEvidence,
    DefenderReturnEvidence,
    MinutesEvidence,
    RoleEvidence,
    StreakEvidence,
    UnderperformanceEvidence,
)
from contracts.injury import InjuryReport
from contracts.market import MarketQuoteInput
from contracts.projection import Projection


class RequestedAdjustment(StrictModel):
    adjustment_id: str
    delta: float | None = None


class AdjustmentDecision(StrictModel):
    """A numeric request this step refused to apply."""

    adjustment_id: str
    market: Market
    applied: Literal[False] = False
    delta: None = None
    reason: str


class FutureContextNote(StrictModel):
    """Pace, usage, and rest stay outside custom-angle adjustments."""

    model_id: str
    adjustment_amount: None = None
    validation_status: Literal["not_modeled"] = "not_modeled"


class RuleFiring(StrictModel):
    rule_id: str
    precedence: int
    matched: bool
    effect: str
    reason: str


class AngleAudit(StrictModel):
    policy_id: str
    status: str
    reason: str
    evidence_snapshot_ids: tuple[str, ...]
    adjustment_amount: None = None
    validation_status: Literal["disabled", "unvalidated"]
    disclosed_gaps: tuple[str, ...] = ()


class AnalysisSubject(StrictModel):
    as_of: UTCDateTime
    market: Market
    snapshot: MarketQuoteInput
    canonical_player_id: str | None
    canonical_game_id: str | None
    injury: InjuryReport | None = None
    availability: AvailabilityEvidence | None = None
    minutes: MinutesEvidence | None = None
    role: RoleEvidence | None = None
    defender_return: DefenderReturnEvidence | None = None
    streak: StreakEvidence | None = None
    underperformance: UnderperformanceEvidence | None = None
    capability: str | None = None
    declared_capabilities: tuple[str, ...] | None = None
    historical_projections: tuple[Projection, ...] = ()
    historical_required: bool = True
    requested_adjustments: tuple[RequestedAdjustment, ...] = ()


class RuleAudit(StrictModel):
    audit_id: str
    rules_version: str
    model_version: str
    rules_fingerprint: str
    as_of: UTCDateTime
    state: AnalysisState
    reason: str
    firings: tuple[RuleFiring, ...]
    angles: tuple[AngleAudit, ...]
    adjustments: tuple[AdjustmentDecision, ...]


class AnalysisResult(StrictModel):
    state: AnalysisState
    reason: str
    canonical_player_id: str | None
    canonical_game_id: str | None
    market: Market
    snapshot_id: str | None
    model_version: str
    rules_version: str
    available_at: UTCDateTime
    essential_gaps: tuple[str, ...] = ()
    disclosed_gaps: tuple[str, ...] = ()
    adjustments: tuple[AdjustmentDecision, ...]
    future_context: tuple[FutureContextNote, ...] = ()
    audit: RuleAudit
