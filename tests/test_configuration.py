from contracts.common import AnalysisState
from contracts.configuration import load_env_example, load_provider_file, load_schedule_file, parse_env_example
from rules.catalog import load_foundation
from support import FIXTURES, ROOT


def test_shipped_configuration_is_available():
    report = load_foundation(ROOT)
    assert report.state is AnalysisState.AVAILABLE
    assert report.reasons == ()
    assert report.model_version == "none"
    assert report.rules_version == "2026-10-29.nba-props-angles"

    provider_status, providers = load_provider_file(ROOT / "config" / "providers.example.yaml")
    assert provider_status.state is AnalysisState.AVAILABLE
    assert providers is not None
    assert all(provider.enabled is False for provider in providers.providers)

    schedule_status, schedules = load_schedule_file(ROOT / "config" / "schedules.example.yaml")
    assert schedule_status.state is AnalysisState.AVAILABLE
    assert schedules is not None
    assert schedules.timezone == "UTC"
    assert all(job.adjustments_enabled is False for job in schedules.jobs)

    env_status, values = load_env_example(ROOT / ".env.example")
    assert env_status.state is AnalysisState.AVAILABLE
    assert values["ODDS_API_KEY"] == ""
    assert values["THE_ODDS_API_KEY"] == ""
    assert values["AWS_ACCESS_KEY_ID"] == ""
    assert values["AWS_SECRET_ACCESS_KEY"] == ""


def test_enabled_provider_is_rejected():
    status, parsed = load_provider_file(FIXTURES / "providers-enabled.yaml")
    assert parsed is None
    assert status.state is AnalysisState.UNAVAILABLE
    assert status.reason == "live_provider_not_implemented"


def test_non_utc_schedule_is_rejected():
    status, parsed = load_schedule_file(FIXTURES / "schedules-timezone.yaml")
    assert parsed is None
    assert status.state is AnalysisState.UNAVAILABLE
    assert status.reason == "unsupported_timezone"


def test_filled_secret_in_env_example_is_rejected():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    status, _values = parse_env_example(text.replace("ODDS_API_KEY=", "ODDS_API_KEY=synthetic-secret"))
    assert status.state is AnalysisState.UNAVAILABLE
    assert status.reason == "secret_present"
