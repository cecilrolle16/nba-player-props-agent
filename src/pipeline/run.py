"""Connect validation through a structured report.

The feature stage calculates historical box scores. The projection stage
can store a baseline mean. When the bundle supplies an integer residual
histogram, the comparison stage classifies that histogram at the quote's
line and side. A separate price record may then store implied probability,
no-vig, probability edge, and expected value. The research policy reads
that record and does not copy the numbers. The legacy decision stays
non-actionable. Stakes and live recommendations stay off.
"""

from contracts.common import AnalysisState
from contracts.comparison import MarketComparison
from contracts.decision import Decision
from contracts.distribution import ProjectionDistribution
from contracts.features import FeatureSnapshot
from contracts.integer_residual import RESIDUAL_VERSION
from contracts.pipeline import PipelineInput, PipelineReport
from contracts.price import PriceComparison
from decision.policy import research_decision
from edge.price import price_quote, priced_quote_from_input, priced_quote_from_snapshot, refused_price
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
    price = _price(bundle, comparison)
    market = bundle.subject.market.value
    return PipelineReport(
        report_id=f"report:synthetic-{market}",
        validation=validation,
        features=features,
        projection=projection,
        comparison=comparison,
        price=price,
        research_decision=research_decision(
            validation=validation,
            comparison=comparison,
            projection=projection,
            price=price,
            policy=bundle.research_policy,
        ),
        decision=decision,
        inputs_eligible=context.eligible,
    )


def _price(bundle: PipelineInput, comparison: MarketComparison) -> PriceComparison | None:
    """Price a residual comparison. A missing histogram leaves the price empty."""

    if comparison.model_version != RESIDUAL_VERSION:
        return None
    quote = priced_quote_from_input(bundle.subject.snapshot)
    if quote is None:
        return refused_price(comparison, "invalid_quote")
    opposite = None if bundle.opposite_quote is None else priced_quote_from_snapshot(bundle.opposite_quote)
    mean = None if bundle.residual_distribution is None else bundle.residual_distribution.distribution_mean
    return price_quote(
        comparison,
        quote,
        decision_cutoff=bundle.subject.as_of,
        opposite=opposite,
        distribution_mean=mean,
    )
