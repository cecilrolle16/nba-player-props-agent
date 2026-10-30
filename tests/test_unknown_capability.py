import socket

from contracts.common import AnalysisState, Capability
from contracts.configuration import load_provider_file
from ingestion.adapters.base import ProviderAdapter
from rules.engine import evaluate
from support import FIXTURES, catalog, load_subject


def test_unknown_capability_in_config_is_unavailable():
    status, parsed = load_provider_file(FIXTURES / "providers-unknown.yaml")
    assert parsed is None
    assert status.state is AnalysisState.UNAVAILABLE
    assert status.reason == "unknown_provider_capability"


def test_unknown_capability_on_adapter_and_in_rules():
    adapter = ProviderAdapter("synthetic-markets", ("markets",), ("points", "assists", "rebounds"))
    decision = adapter.check("live_bets")
    assert decision.state is AnalysisState.UNAVAILABLE
    assert decision.reason == "unknown_provider_capability"
    fetched = adapter.fetch("live_bets")
    assert fetched.reason == "unknown_provider_capability"
    assert fetched.records == ()

    subject = load_subject("valid_points.json").model_copy(update={"capability": "live_bets"})
    result = evaluate(catalog(), subject)
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "unknown_provider_capability"


def test_known_but_undeclared_capability_is_unsupported():
    adapter = ProviderAdapter("synthetic-schedule", ("schedules",))
    decision = adapter.check("markets", market="points")
    assert decision.reason == "unsupported_capability"
    subject = load_subject("valid_points.json").model_copy(
        update={"capability": "markets", "declared_capabilities": ("schedules",)}
    )
    result = evaluate(catalog(), subject)
    assert result.reason == "unsupported_capability"


def test_declared_market_outside_scope_is_unsupported():
    adapter = ProviderAdapter("synthetic-markets", ("markets",), ("points",))
    decision = adapter.check(Capability.MARKETS.value, market="assists")
    assert decision.state is AnalysisState.UNAVAILABLE
    assert decision.reason == "unsupported_market"


def test_fetch_does_not_call_the_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    adapter = ProviderAdapter("synthetic-schedule", ("schedules",))
    result = adapter.fetch("schedules")
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "live_integration_not_implemented"


def test_sources_do_not_import_network_clients():
    banned = ("urllib", "httpx", "requests", "boto3", "socket", "aiohttp")
    for path in (FIXTURES.parents[1] / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in banned:
            assert f"import {name}" not in text
            assert f"from {name}" not in text
