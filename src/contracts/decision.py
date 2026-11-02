"""Decision record. Input eligibility is not a recommendation.

`threshold`, `bankroll_sizing`, and `stake` are fixed null. That is a
temporary restriction until a threshold and a bankroll module exist.
`edge` and `ev_per_unit` are extension fields an injected synthetic stage
may fill. A calculated price edge stays on the price record and is not
copied here. live_recommendation and actionable stay false.
"""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import (
    UTCDateTime,
    AnalysisState,
    ComparisonId,
    DecisionId,
    ProjectionId,
    SnapshotId,
    StrictModel,
)
from contracts.stages import CalibrationStatus, ValueOrigin


class Decision(StrictModel):
    decision_id: DecisionId
    result: Literal["pass", "abstain", "synthetic_candidate"]
    reason: str = Field(min_length=1)
    analysis_state: AnalysisState
    inputs_eligible: bool
    live_recommendation: Literal[False] = False
    actionable: Literal[False] = False
    threshold: None = None
    bankroll_sizing: None = None
    stake: None = None
    edge: float | None = Field(default=None, allow_inf_nan=False)
    ev_per_unit: float | None = Field(default=None, allow_inf_nan=False)
    comparison_id: ComparisonId
    canonical_projection_id: ProjectionId
    odds_snapshot_id: SnapshotId
    calibration_status: CalibrationStatus
    origin: ValueOrigin
    audit_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    rules_version: str = Field(min_length=1)
    model_version: Literal["none"]
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: UTCDateTime

    @model_validator(mode="after")
    def separate_eligibility_from_recommendation(self) -> "Decision":
        if self.inputs_eligible != (self.analysis_state is AnalysisState.AVAILABLE):
            raise ValueError("inputs_eligible follows the rules state and is not a recommendation")
        numbered = self.edge is not None or self.ev_per_unit is not None
        if self.result == "synthetic_candidate":
            if not self.inputs_eligible:
                raise ValueError("a synthetic candidate requires inputs that passed the gates")
            if self.origin is not ValueOrigin.SYNTHETIC or self.calibration_status is not CalibrationStatus.UNCALIBRATED:
                raise ValueError("a synthetic candidate is uncalibrated and is not a live recommendation")
            if self.edge is None or self.ev_per_unit is None:
                raise ValueError("a synthetic candidate records the injected edge and EV")
        else:
            if numbered or self.origin is not ValueOrigin.NOT_IMPLEMENTED:
                raise ValueError("pass and abstain do not carry calculated edge or EV")
            if self.calibration_status is not CalibrationStatus.NOT_APPLICABLE:
                raise ValueError("pass and abstain are not calibrated")
        if self.result == "abstain" and self.inputs_eligible:
            raise ValueError("abstain is the result when inputs are not eligible")
        if self.result == "pass" and not self.inputs_eligible:
            raise ValueError("ineligible inputs abstain")
        return self
