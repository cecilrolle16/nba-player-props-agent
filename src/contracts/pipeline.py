"""Inputs and the structured report for one synthetic pipeline run."""

from typing import Literal

from pydantic import model_validator

from contracts.analysis import AnalysisResult, AnalysisSubject
from contracts.common import ReportId, SnapshotId, StrictModel
from contracts.comparison import MarketComparison
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution
from contracts.features import FeatureSnapshot
from contracts.player_log import PlayerGameLog
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame


class PipelineInput(StrictModel):
    subject: AnalysisSubject
    schedule: ScheduleGame
    roster: RosterEntry
    player_logs: tuple[PlayerGameLog, ...]
    log_snapshot_ids: tuple[SnapshotId, ...] = ()


class PipelineReport(StrictModel):
    report_id: ReportId
    validation: AnalysisResult
    features: FeatureSnapshot
    projection: ProjectionDistribution
    comparison: MarketComparison
    decision: Decision
    settlement: None = None
    live_recommendation: Literal[False] = False
    recommendation: Literal["none"] = "none"
    inputs_eligible: bool

    @model_validator(mode="after")
    def eligibility_matches_the_decision(self) -> "PipelineReport":
        if self.inputs_eligible != self.decision.inputs_eligible:
            raise ValueError("the report and the decision share one eligibility flag")
        if self.inputs_eligible != (self.validation.state.value == "available"):
            raise ValueError("eligibility is the rules state, not a recommendation")
        return self
