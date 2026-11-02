"""Bedrock explanation contract. It does not change a numerical report.

The Python report stays the source of the research result, calibration
status, synthetic label, and every number. A model explanation may only
restate those facts and cite source ids that were already on the input.
"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from contracts.common import StrictModel
from contracts.stages import CalibrationStatus


class BedrockSettings(StrictModel):
    """Portable Bedrock client bounds. Credentials are not fields here."""

    version: Literal[1]
    enabled: bool
    region: str = Field(pattern=r"^[a-z]{2}(-[a-z0-9]+)+-\d$")
    model_id: str = Field(min_length=3, max_length=256)
    prompt_path: str = Field(pattern=r"^prompts/[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.txt$")
    connect_timeout_seconds: float = Field(gt=0, le=30)
    read_timeout_seconds: float = Field(gt=0, le=120)
    max_attempts: int = Field(ge=1, le=4)
    max_tokens: int = Field(ge=1, le=4000)
    max_output_chars: int = Field(ge=1, le=20000)
    temperature: float = Field(ge=0, le=1)

    @field_validator("model_id")
    @classmethod
    def model_id_is_not_a_secret(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("model id must be a single token")
        lowered = value.lower()
        if "akia" in lowered or "secret" in lowered or "aws_access_key" in lowered:
            raise ValueError("model id must not contain a credential")
        return value

    @field_validator("prompt_path")
    @classmethod
    def prompt_path_stays_in_the_repository(cls, value: str) -> str:
        if ".." in value or value.startswith(("/", "\\")):
            raise ValueError("prompt path must stay inside the repository")
        return value


class UntrustedText(StrictModel):
    """Provider wording. It is data, not an instruction."""

    field: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    source_id: str = Field(min_length=1, max_length=120)


class ReasoningClaim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    source_ids: tuple[str, ...] = Field(min_length=1)


class ReasoningInput(StrictModel):
    """Validated evidence and the finished Python report. Numbers are already fixed."""

    data_label: Literal["synthetic", "observed"]
    research_result: Literal["research_candidate", "no_bet", "unavailable"]
    research_reason: str = Field(min_length=1)
    calibration_status: CalibrationStatus
    live_recommendation: Literal[False] = False
    stake: None = None
    numerical_report_json: str = Field(min_length=2)
    audit_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_audits: tuple[str, ...] = Field(min_length=3, max_length=3)
    source_ids: tuple[str, ...] = Field(min_length=1)
    risks: tuple[str, ...] = ()
    data_gaps: tuple[str, ...] = ()
    untrusted_text: tuple[UntrustedText, ...] = ()

    @field_validator("numerical_report_json")
    @classmethod
    def report_is_a_json_object(cls, value: str) -> str:
        import json

        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("the numerical report is a JSON object")
        return value


class ReasoningExplanation(StrictModel):
    """A model explanation, or the deterministic fallback. The report hash is Python's."""

    explanation_id: Literal["explanation:analysis-summary"] = "explanation:analysis-summary"
    status: Literal["explained", "fallback"]
    fallback_reason: str | None = None
    numerical_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_result: Literal["research_candidate", "no_bet", "unavailable"]
    research_reason: str = Field(min_length=1)
    data_label: Literal["synthetic", "observed"]
    calibration_status: CalibrationStatus
    live_recommendation: Literal[False] = False
    stake: None = None
    recommendations_enabled: Literal[False] = False
    claims: tuple[ReasoningClaim, ...] = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str | None = None
    region: str = Field(min_length=1)

    @model_validator(mode="after")
    def fallback_names_a_reason(self) -> "ReasoningExplanation":
        if self.status == "fallback" and not self.fallback_reason:
            raise ValueError("a fallback names the reason")
        if self.status == "explained" and self.fallback_reason is not None:
            raise ValueError("an accepted explanation has no fallback reason")
        if self.status == "explained" and not self.model_id:
            raise ValueError("an accepted explanation names the model id")
        return self
