"""Provider boundary. Fetch never leaves the process."""

from contracts.capabilities import CapabilityDecision, classify_capability, declaration_status
from contracts.common import AnalysisState, StrictModel


class FetchResult(StrictModel):
    state: AnalysisState
    reason: str | None
    capability: str
    records: tuple[str, ...] = ()


class ProviderAdapter:
    """Declares capabilities and refuses live collection."""

    def __init__(
        self,
        provider_id: str,
        capabilities: tuple[str, ...] | list[str],
        supported_markets: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.provider_id = provider_id
        self.capabilities = tuple(capabilities)
        self.supported_markets = tuple(supported_markets)

    def declaration(self) -> CapabilityDecision:
        return declaration_status(self.capabilities)

    def check(self, capability: str, market: str | None = None) -> CapabilityDecision:
        return classify_capability(
            capability,
            declared=self.capabilities,
            market=market,
            supported_markets=self.supported_markets,
        )

    def fetch(self, capability: str, market: str | None = None) -> FetchResult:
        decision = self.check(capability, market)
        if decision.state is not AnalysisState.AVAILABLE:
            return FetchResult(state=decision.state, reason=decision.reason, capability=capability)
        return FetchResult(
            state=AnalysisState.UNAVAILABLE,
            reason="live_integration_not_implemented",
            capability=capability,
        )
