"""Default stage results. Injected replacements are supplied by tests."""

from dataclasses import dataclass
from typing import Protocol

from contracts.analysis import AnalysisResult
from contracts.common import SNAPSHOT_ID, SourceId
from contracts.comparison import MarketComparison
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution
from contracts.features import FeatureSnapshot
from contracts.pipeline import PipelineInput
from contracts.stages import CalibrationStatus, StageState, ValueOrigin
from rules.catalog import RuleCatalog


@dataclass(frozen=True)
class StageContext:
    bundle: PipelineInput
    catalog: RuleCatalog
    validation: AnalysisResult
    eligible: bool


class FeatureStage(Protocol):
    def __call__(self, context: StageContext) -> FeatureSnapshot:
        """Return a feature snapshot. The default copies references and computes no features."""


class DistributionStage(Protocol):
    def __call__(self, context: StageContext, features: FeatureSnapshot) -> ProjectionDistribution:
        """Return a stat distribution. The default leaves outcomes empty."""


class ComparisonStage(Protocol):
    def __call__(self, context: StageContext, projection: ProjectionDistribution) -> MarketComparison:
        """Return one price. The default leaves side probabilities null."""


class DecisionStage(Protocol):
    def __call__(
        self,
        context: StageContext,
        features: FeatureSnapshot,
        projection: ProjectionDistribution,
        comparison: MarketComparison,
    ) -> Decision:
        """Return pass, abstain, or a synthetic candidate. The default does not price edge or EV."""


@dataclass(frozen=True)
class PipelineStages:
    """Replacement callables are the extension points. None uses the default, which does not calculate."""

    features: FeatureStage | None = None
    projection: DistributionStage | None = None
    comparison: ComparisonStage | None = None
    decision: DecisionStage | None = None


def _quote_snapshot(bundle: PipelineInput) -> str:
    snapshot_id = bundle.subject.snapshot.snapshot_id
    if isinstance(snapshot_id, str) and SNAPSHOT_ID.fullmatch(snapshot_id):
        return snapshot_id
    return "snapshot:unavailable"


def _evidence_ids(bundle: PipelineInput) -> tuple[str, ...]:
    found: list[str] = []
    subject = bundle.subject
    records = (
        subject.availability,
        subject.minutes,
        subject.role,
        subject.defender_return,
        subject.streak,
        subject.underperformance,
    )
    names = (
        "snapshot_id",
        "return_date_snapshot_id",
        "minutes_snapshot_id",
        "key_defender_snapshot_id",
        "defensive_rotation_snapshot_id",
    )
    for record in records:
        if record is None:
            continue
        for name in names:
            value = getattr(record, name, None)
            if isinstance(value, str) and SNAPSHOT_ID.fullmatch(value) and value not in found:
                found.append(value)
    return tuple(found)


def _state(eligible: bool) -> StageState:
    if eligible:
        return StageState.NOT_IMPLEMENTED
    return StageState.UNAVAILABLE


def default_features(context: StageContext) -> FeatureSnapshot:
    bundle = context.bundle
    subject = bundle.subject
    market = subject.market.value
    return FeatureSnapshot(
        feature_id=f"feature:not-implemented-{market}",
        canonical_player_id=subject.canonical_player_id,
        canonical_game_id=subject.canonical_game_id,
        market=subject.market,
        as_of=subject.as_of,
        schedule_snapshot_id=bundle.schedule.snapshot_id,
        schedule_tip_at=bundle.schedule.tip_at,
        roster_available_at=bundle.roster.available_at,
        log_snapshot_ids=bundle.log_snapshot_ids,
        evidence_snapshot_ids=_evidence_ids(bundle),
        quote_snapshot_id=_quote_snapshot(bundle),
        quote_captured_at=subject.snapshot.captured_at,
        quote_available_at=subject.snapshot.available_at,
        rules_version=context.catalog.rules_version,
        model_version="none",
        config_fingerprint=context.catalog.source_sha256,
        stage_state=_state(context.eligible),
        origin=ValueOrigin.NOT_IMPLEMENTED,
        calibration_status=CalibrationStatus.NOT_APPLICABLE,
        available_at=subject.as_of,
    )


def default_projection(context: StageContext, features: FeatureSnapshot) -> ProjectionDistribution:
    market = context.bundle.subject.market.value
    return ProjectionDistribution(
        canonical_projection_id=f"projection:not-implemented-{market}",
        feature_id=features.feature_id,
        canonical_player_id=features.canonical_player_id,
        canonical_game_id=features.canonical_game_id,
        market=features.market,
        rules_version=context.catalog.rules_version,
        model_version="none",
        stage_state=_state(context.eligible),
        origin=ValueOrigin.NOT_IMPLEMENTED,
        calibration_status=CalibrationStatus.NOT_APPLICABLE,
        projected_at=None,
        available_at=None,
        source_ids=(SourceId(provider_id="pipeline", native_id=f"stat-distribution-not-implemented-{market}"),),
    )


def default_comparison(context: StageContext, projection: ProjectionDistribution) -> MarketComparison:
    subject = context.bundle.subject
    market = subject.market.value
    sources = subject.snapshot.source_ids or (
        SourceId(provider_id="pipeline", native_id=f"comparison-not-implemented-{market}"),
    )
    return MarketComparison(
        comparison_id=f"comparison:not-implemented-{market}",
        canonical_projection_id=projection.canonical_projection_id,
        canonical_player_id=subject.canonical_player_id,
        canonical_game_id=subject.canonical_game_id,
        market=subject.market,
        odds_snapshot_id=_quote_snapshot(context.bundle),
        quote_captured_at=subject.snapshot.captured_at,
        quote_available_at=subject.snapshot.available_at,
        rules_version=context.catalog.rules_version,
        model_version="none",
        stage_state=_state(context.eligible),
        origin=ValueOrigin.NOT_IMPLEMENTED,
        calibration_status=CalibrationStatus.NOT_APPLICABLE,
        source_ids=sources,
    )


def default_decision(
    context: StageContext,
    features: FeatureSnapshot,
    projection: ProjectionDistribution,
    comparison: MarketComparison,
) -> Decision:
    del features
    validation = context.validation
    if context.eligible:
        result = "pass"
        reason = "not_implemented"
    else:
        result = "abstain"
        reason = validation.reason
    return Decision(
        decision_id=f"decision:{result}-{context.bundle.subject.market.value}",
        result=result,
        reason=reason,
        analysis_state=validation.state,
        inputs_eligible=context.eligible,
        comparison_id=comparison.comparison_id,
        canonical_projection_id=projection.canonical_projection_id,
        odds_snapshot_id=comparison.odds_snapshot_id,
        calibration_status=CalibrationStatus.NOT_APPLICABLE,
        origin=ValueOrigin.NOT_IMPLEMENTED,
        audit_id=validation.audit.audit_id,
        rules_version=context.catalog.rules_version,
        model_version="none",
        config_fingerprint=context.catalog.source_sha256,
        as_of=context.bundle.subject.as_of,
    )
