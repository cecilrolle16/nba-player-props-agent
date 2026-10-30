"""Capability checks shared by configuration, adapters, and rules."""

from contracts.common import (
    KNOWN_CAPABILITIES,
    MARKET_CAPABILITIES,
    AnalysisState,
    Capability,
    Market,
    StrictModel,
)


class CapabilityDecision(StrictModel):
    state: AnalysisState
    reason: str | None
    capability: str


def classify_capability(
    capability: str,
    declared: tuple[str, ...] | None = None,
    market: str | None = None,
    supported_markets: tuple[str, ...] = (),
) -> CapabilityDecision:
    if capability not in KNOWN_CAPABILITIES:
        return CapabilityDecision(
            state=AnalysisState.UNAVAILABLE,
            reason="unknown_provider_capability",
            capability=capability,
        )
    if declared is not None and capability not in declared:
        return CapabilityDecision(
            state=AnalysisState.UNAVAILABLE,
            reason="unsupported_capability",
            capability=capability,
        )
    if market is not None and capability in MARKET_CAPABILITIES:
        known_markets = {item.value for item in Market}
        if market not in known_markets:
            return CapabilityDecision(
                state=AnalysisState.UNAVAILABLE,
                reason="unknown_market",
                capability=capability,
            )
        if market not in supported_markets:
            return CapabilityDecision(
                state=AnalysisState.UNAVAILABLE,
                reason="unsupported_market",
                capability=capability,
            )
    return CapabilityDecision(state=AnalysisState.AVAILABLE, reason=None, capability=capability)


def declaration_status(declared: tuple[str, ...]) -> CapabilityDecision:
    for name in declared:
        if name not in KNOWN_CAPABILITIES:
            return CapabilityDecision(
                state=AnalysisState.UNAVAILABLE,
                reason="unknown_provider_capability",
                capability=name,
            )
    return CapabilityDecision(
        state=AnalysisState.AVAILABLE,
        reason=None,
        capability=Capability.SCHEDULES.value if not declared else declared[0],
    )
