"""Inputs and the structured report for one synthetic pipeline run."""

from typing import Literal

from pydantic import Field, model_validator

from contracts.analysis import AnalysisResult, AnalysisSubject
from contracts.common import UTCDateTime, ReportId, SnapshotId, StrictModel
from contracts.comparison import MarketComparison
from contracts.configuration import BaselineProjectionSettings, FeatureWindowSettings, ResearchDecisionSettings
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution
from contracts.features import FeatureSnapshot
from contracts.integer_residual import IntegerResidualDistribution
from contracts.market import MarketSnapshot
from contracts.player_log import PlayerGameLog
from contracts.price import PriceComparison
from contracts.research import ResearchDecision
from contracts.projection import RatePrior
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame


class PipelineInput(StrictModel):
    subject: AnalysisSubject
    schedule: ScheduleGame
    roster: RosterEntry
    player_logs: tuple[PlayerGameLog, ...]
    log_snapshot_ids: tuple[SnapshotId, ...] = ()
    history_games: tuple[ScheduleGame, ...] = ()
    feature_windows: FeatureWindowSettings | None = None
    baseline_settings: BaselineProjectionSettings | None = None
    rate_prior: RatePrior | None = None
    minutes_uncertainty: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    residual_distribution: IntegerResidualDistribution | None = None
    distribution_available_at: UTCDateTime | None = None
    opposite_quote: MarketSnapshot | None = None
    research_policy: ResearchDecisionSettings | None = None


class PipelineReport(StrictModel):
    report_id: ReportId
    validation: AnalysisResult
    features: FeatureSnapshot
    projection: ProjectionDistribution
    comparison: MarketComparison
    price: PriceComparison | None = None
    research_decision: ResearchDecision
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
