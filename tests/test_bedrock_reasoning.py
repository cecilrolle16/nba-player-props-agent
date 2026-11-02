"""Bedrock explanation tests. The client is mocked. No sports API is called."""

import json
from pathlib import Path

from contracts.configuration import load_bedrock
from contracts.reasoning import BedrockSettings, ReasoningInput, UntrustedText
from contracts.stages import CalibrationStatus
from reasoning.bedrock import explain_report, load_runtime_settings
from support import ROOT

AUDIT = "a" * 64
SOURCE = "snapshot:synthetic-points-1"
SECRET = "super-secret-odds-value"


class FakeRuntime:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def converse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _settings(**updates: object) -> BedrockSettings:
    payload = {
        "version": 1,
        "enabled": True,
        "region": "us-west-2",
        "model_id": "us.anthropic.claude-3-haiku-20240307-v1:0",
        "prompt_path": "prompts/analysis-summary.txt",
        "connect_timeout_seconds": 5,
        "read_timeout_seconds": 20,
        "max_attempts": 2,
        "max_tokens": 800,
        "max_output_chars": 4000,
        "temperature": 0,
    }
    payload.update(updates)
    return BedrockSettings.model_validate(payload)


def _evidence(**updates: object) -> ReasoningInput:
    payload = {
        "data_label": "synthetic",
        "research_result": "unavailable",
        "research_reason": "missing_essential_evidence",
        "calibration_status": CalibrationStatus.UNCALIBRATED,
        "live_recommendation": False,
        "stake": None,
        "numerical_report_json": json.dumps({"line": 24.5, "ev_per_unit": None}, sort_keys=True),
        "audit_id": AUDIT,
        "rule_audits": ("defender_return", "no_streak_chasing", "underperformance_rebound"),
        "source_ids": (SOURCE,),
        "risks": ("experimental_uncalibrated",),
        "data_gaps": ("missing_essential_evidence",),
        "untrusted_text": (
            UntrustedText(
                field="raw_player_name",
                text=f"Synthetic Guard A THE_ODDS_API_KEY={SECRET}",
                source_id=SOURCE,
            ),
        ),
    }
    payload.update(updates)
    return ReasoningInput.model_validate(payload)


def _reply(body: dict[str, object], *, stop_reason: str = "end_turn") -> dict[str, object]:
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": [{"text": json.dumps(body)}]}},
    }


def _valid_body(**updates: object) -> dict[str, object]:
    body = {
        "research_result": "unavailable",
        "data_label": "synthetic",
        "calibration_status": "uncalibrated",
        "live_recommendation": False,
        "claims": [
            {
                "text": "Synthetic uncalibrated outcome for snapshot:synthetic-points-1 at line 24.5.",
                "source_ids": [SOURCE],
            }
        ],
    }
    body.update(updates)
    return body


def test_valid_explanation_keeps_the_python_report_and_redacts_secrets(monkeypatch):
    monkeypatch.setenv("THE_ODDS_API_KEY", SECRET)
    evidence = _evidence()
    client = FakeRuntime([_reply(_valid_body())])
    explained = explain_report(evidence, _settings(), client=client)
    assert explained.status == "explained"
    assert explained.research_result == "unavailable"
    assert explained.data_label == "synthetic"
    assert explained.calibration_status is CalibrationStatus.UNCALIBRATED
    assert explained.live_recommendation is False
    assert explained.stake is None
    assert explained.recommendations_enabled is False
    assert explained.model_id == "us.anthropic.claude-3-haiku-20240307-v1:0"
    assert explained.claims[0].source_ids == (SOURCE,)
    sent = json.dumps(client.calls)
    assert SECRET not in sent
    assert "THE_ODDS_API_KEY" not in sent or "[redacted]" in sent
    assert "[redacted]" in sent
    assert "UNTRUSTED" in sent


def test_fenced_json_is_accepted_when_the_object_matches_the_report():
    body = json.dumps(_valid_body())
    fenced = {
        "stopReason": "end_turn",
        "output": {"message": {"role": "assistant", "content": [{"text": f"```json\n{body}\n```"}]}},
    }
    explained = explain_report(_evidence(), _settings(), client=FakeRuntime([fenced]))
    assert explained.status == "explained"
    assert explained.research_result == "unavailable"
    assert explained.live_recommendation is False


def test_invalid_reference_fabricated_number_and_abstention_override_fall_back():
    evidence = _evidence()
    settings = _settings()
    invalid = explain_report(
        evidence,
        settings,
        client=FakeRuntime([_reply(_valid_body(claims=[{"text": "Synthetic uncalibrated line 24.5.", "source_ids": ["snapshot:missing"]}]))]),
    )
    assert invalid.status == "fallback"
    assert invalid.fallback_reason == "invalid_reference"
    assert invalid.research_result == "unavailable"

    fabricated = explain_report(
        evidence,
        settings,
        client=FakeRuntime(
            [
                _reply(
                    _valid_body(
                        claims=[
                            {
                                "text": "Synthetic uncalibrated probability 0.99 at line 24.5.",
                                "source_ids": [SOURCE],
                            }
                        ]
                    )
                )
            ]
        ),
    )
    assert fabricated.fallback_reason == "fabricated_numeric_claim"
    assert fabricated.research_result == "unavailable"
    assert "0.99" not in fabricated.claims[0].text

    override = explain_report(
        evidence,
        settings,
        client=FakeRuntime([_reply(_valid_body(research_result="research_candidate"))]),
    )
    assert override.fallback_reason == "abstention_override"
    assert override.research_result == "unavailable"
    assert override.live_recommendation is False
    assert override.data_label == "synthetic"


def test_synthetic_label_and_calibration_cannot_be_upgraded():
    evidence = _evidence()
    settings = _settings()
    relabeled = explain_report(
        evidence,
        settings,
        client=FakeRuntime([_reply(_valid_body(data_label="observed"))]),
    )
    assert relabeled.fallback_reason == "synthetic_label_removed"
    assert relabeled.data_label == "synthetic"

    upgraded = explain_report(
        evidence,
        settings,
        client=FakeRuntime([_reply(_valid_body(calibration_status="calibrated"))]),
    )
    assert upgraded.fallback_reason == "calibration_upgrade"
    assert upgraded.calibration_status is CalibrationStatus.UNCALIBRATED


def test_timeout_and_malformed_output_keep_the_structured_result():
    evidence = _evidence()
    timed = FakeRuntime([TimeoutError("timed out"), TimeoutError("timed out")])
    timeout = explain_report(evidence, _settings(), client=timed)
    assert len(timed.calls) == 2
    assert timeout.status == "fallback"
    assert timeout.fallback_reason == "timeout"
    assert timeout.research_result == "unavailable"

    malformed = explain_report(
        evidence,
        _settings(),
        client=FakeRuntime([{"stopReason": "end_turn", "output": {"message": {"content": [{"text": "not-json"}]}}}]),
    )
    assert malformed.fallback_reason == "malformed_response"
    assert malformed.stake is None

    truncated = explain_report(
        evidence,
        _settings(),
        client=FakeRuntime([_reply(_valid_body(), stop_reason="max_tokens")]),
    )
    assert truncated.fallback_reason == "output_too_long"


def test_disabled_configuration_does_not_call_bedrock_and_the_pipeline_does_not_import_it():
    client = FakeRuntime([_reply(_valid_body())])
    evidence = _evidence()
    fallback = explain_report(evidence, _settings(enabled=False), client=client)
    assert client.calls == []
    assert fallback.fallback_reason == "reasoning_disabled"
    assert fallback.research_result == "unavailable"
    assert "Synthetic" in fallback.claims[0].text
    assert fallback.live_recommendation is False

    pipeline = Path(ROOT / "src" / "pipeline" / "run.py").read_text(encoding="utf-8")
    assert "import boto3" not in pipeline
    assert "explain_report" in pipeline

    status, parsed = load_bedrock(ROOT / "config" / "bedrock.example.yaml")
    assert status.state.value == "available"
    assert parsed is not None
    assert parsed.enabled is False
    policy = json.loads((ROOT / "config" / "bedrock-iam-policy.example.json").read_text(encoding="utf-8"))
    encoded = json.dumps(policy)
    assert "bedrock:InvokeModel" in encoded
    assert "ACCOUNT_ID" in encoded
    assert SECRET not in encoded


def test_bedrock_file_override_stays_on_the_disabled_example(monkeypatch):
    monkeypatch.setenv("NBA_BEDROCK_FILE", "config/bedrock.example.yaml")
    settings = load_runtime_settings(ROOT)
    assert settings.enabled is False
    assert settings.region == "us-west-2"
