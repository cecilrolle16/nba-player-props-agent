"""Default stage results. Injected replacements are supplied by tests."""

from dataclasses import dataclass
from typing import Protocol

from contracts.analysis import AnalysisResult
from contracts.common import SNAPSHOT_ID, SourceId
from contracts.comparison import MarketComparison
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution
from contracts.features import FeatureSnapshot
from contracts.projection import ExpectedMinutesInput
from features.historical import DEFAULT_WINDOWS, appearances_from_logs, build_historical_features
from projection.baseline import DEFAULT_BASELINE, project_baseline
from projection.side_probability import side_probabilities
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
        """Return a feature snapshot. The default calculates historical box scores and does not project."""


class DistributionStage(Protocol):
    def __call__(self, context: StageContext, features: FeatureSnapshot) -> ProjectionDistribution:
        """Return a projection. The default may set a baseline mean and leaves outcomes empty."""


class ComparisonStage(Protocol):
    def __call__(self, context: StageContext, projection: ProjectionDistribution) -> MarketComparison:
        """Return one quote. The default may classify a supplied integer histogram and does not price it."""


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


def reference_features(context: StageContext) -> FeatureSnapshot:
    """Snapshot clocks and source references, with no historical calculation."""

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


def default_features(context: StageContext) -> FeatureSnapshot:
    shell = reference_features(context)
    if not context.eligible:
        return shell
    bundle = context.bundle
    subject = bundle.subject
    windows = bundle.feature_windows or DEFAULT_WINDOWS
    calculated = build_historical_features(
        appearances=appearances_from_logs(
            logs=bundle.player_logs,
            log_snapshot_ids=bundle.log_snapshot_ids,
            target_game=bundle.schedule,
            history_games=bundle.history_games,
        ),
        target_player_id=subject.canonical_player_id,
        target_game_id=subject.canonical_game_id,
        target_tip_at=bundle.schedule.tip_at,
        cutoff=subject.as_of,
        windows=windows,
    )
    return shell.model_copy(
        update={
            "feature_id": f"feature:historical-{subject.market.value}",
            "stage_state": StageState.CALCULATED,
            "origin": ValueOrigin.OBSERVED,
            "calibration_status": CalibrationStatus.NOT_APPLICABLE,
            **calculated,
        }
    )


def reference_projection(context: StageContext, features: FeatureSnapshot) -> ProjectionDistribution:
    """Projection shell with no mean and no outcome distribution."""

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


def default_projection(context: StageContext, features: FeatureSnapshot) -> ProjectionDistribution:
    shell = reference_projection(context, features)
    if not context.eligible or features.stage_state is not StageState.CALCULATED:
        return shell
    settings = context.bundle.baseline_settings or DEFAULT_BASELINE
    calculated = project_baseline(
        features=features,
        minutes=_expected_minutes(context),
        prior=context.bundle.rate_prior,
        shrinkage_enabled=settings.shrinkage_enabled,
        cutoff=context.bundle.subject.as_of,
    )
    return shell.model_copy(update=calculated)


def _expected_minutes(context: StageContext) -> ExpectedMinutesInput | None:
    subject = context.bundle.subject
    evidence = subject.minutes
    if subject.canonical_player_id is None or subject.canonical_game_id is None or evidence is None:
        return None
    return ExpectedMinutesInput(
        canonical_player_id=subject.canonical_player_id,
        canonical_game_id=subject.canonical_game_id,
        market=subject.market,
        known=evidence.known,
        expected_minutes=evidence.expected_minutes,
        minutes_uncertainty=context.bundle.minutes_uncertainty,
        snapshot_id=evidence.snapshot_id,
        available_at=evidence.available_at,
    )


def default_comparison(context: StageContext, projection: ProjectionDistribution) -> MarketComparison:
    subject = context.bundle.subject
    market = subject.market.value
    sources = subject.snapshot.source_ids or (
        SourceId(provider_id="pipeline", native_id=f"comparison-not-implemented-{market}"),
    )
    shell = MarketComparison(
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
    distribution = context.bundle.residual_distribution
    if (
        not context.eligible
        or distribution is None
        or subject.canonical_player_id is None
        or subject.canonical_game_id is None
    ):
        return shell
    quote = subject.snapshot
    return side_probabilities(
        distribution,
        canonical_player_id=subject.canonical_player_id,
        canonical_game_id=subject.canonical_game_id,
        market=subject.market,
        line=quote.line,
        side=quote.side,
        odds_snapshot_id=_quote_snapshot(context.bundle),
        quote_captured_at=quote.captured_at,
        quote_available_at=quote.available_at,
        distribution_available_at=context.bundle.distribution_available_at,
        decision_cutoff=subject.as_of,
        rules_version=context.catalog.rules_version,
        source_ids=quote.source_ids,
        quote_player_id=quote.canonical_player_id,
        quote_game_id=quote.canonical_game_id,
        quote_market=quote.market,
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
