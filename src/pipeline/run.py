"""Connect validation through a structured report. Unfinished stages add no numbers."""

from contracts.common import AnalysisState
from contracts.comparison import MarketComparison
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution
from contracts.features import FeatureSnapshot
from contracts.pipeline import PipelineInput, PipelineReport
from contracts.stages import StageState
from pipeline.stages import (
    PipelineStages,
    StageContext,
    default_comparison,
    default_decision,
    default_features,
    default_projection,
)
from rules.catalog import RuleCatalog
from rules.engine import evaluate


def _guard_decision(
    decision: Decision,
    context: StageContext,
    features: FeatureSnapshot,
    projection: ProjectionDistribution,
    comparison: MarketComparison,
) -> Decision:
    unfinished = any(
        item.stage_state is not StageState.SYNTHETIC for item in (features, projection, comparison)
    )
    if context.eligible and not unfinished and decision.result == "synthetic_candidate":
        return decision
    if not context.eligible:
        return default_decision(context, features, projection, comparison)
    if unfinished or decision.result != "pass":
        return default_decision(context, features, projection, comparison)
    return decision


def run_pipeline(
    bundle: PipelineInput,
    catalog: RuleCatalog,
    stages: PipelineStages | None = None,
) -> PipelineReport:
    """Validate with the existing rules, then record each later stage.

    Injected stages may supply synthetic uncalibrated values. A stage that is
    not synthetic cannot turn the run into a recommendation. Settlement is not
    produced.
    """

    validation = evaluate(catalog, bundle.subject)
    context = StageContext(
        bundle=bundle,
        catalog=catalog,
        validation=validation,
        eligible=validation.state is AnalysisState.AVAILABLE,
    )
    selected = stages or PipelineStages()
    features = selected.features(context) if selected.features is not None else default_features(context)
    projection = (
        selected.projection(context, features)
        if selected.projection is not None
        else default_projection(context, features)
    )
    comparison = (
        selected.comparison(context, projection)
        if selected.comparison is not None
        else default_comparison(context, projection)
    )
    proposed = (
        selected.decision(context, features, projection, comparison)
        if selected.decision is not None
        else default_decision(context, features, projection, comparison)
    )
    decision = _guard_decision(proposed, context, features, projection, comparison)
    market = bundle.subject.market.value
    return PipelineReport(
        report_id=f"report:synthetic-{market}",
        validation=validation,
        features=features,
        projection=projection,
        comparison=comparison,
        decision=decision,
        inputs_eligible=context.eligible,
    )
