"""Load the versioned rules catalog."""

from hashlib import sha256
import json
from pathlib import Path

from typing import Literal

from pydantic import Field, ValidationError, model_validator

from contracts.common import AnalysisState, Market, StrictModel
from contracts.configuration import (
    ConfigStatus,
    load_env_example,
    load_nba_api_log_settings,
    load_nba_api_schedule_settings,
    load_odds_api_settings,
    load_odds_api_teams,
    load_provider_file,
    load_schedule_file,
    load_team_abbreviations,
)

REQUIRED_RULE_IDS = frozenset(
    {
        "unknown_provider_capability",
        "unsupported_capability",
        "not_yet_available",
        "malformed_odds",
        "unresolved_player_id",
        "stale_injury_report",
        "missing_essential_evidence",
        "missing_historical_projections",
    }
)
FUTURE_CONTEXT_MODELS = frozenset({"pace", "usage", "rest"})
DEFENDER_FACTS = frozenset(
    {
        "confirmed_return_date",
        "games_since_return",
        "minutes_since_return",
        "key_defensive_player",
    }
)
STREAK_EVIDENCE = frozenset(
    {"minutes", "usage", "matchup", "shot_quality", "health", "game_environment"}
)
REBOUND_STABILITY = frozenset({"minutes", "usage", "shot_volume"})
REBOUND_EXCLUSIONS = frozenset(
    {"injury", "illness", "rest", "reduced_minutes", "blowouts", "declining_production_drivers"}
)


class RuleSpec(StrictModel):
    id: str
    precedence: int = Field(ge=0)
    effect: str
    reason: str
    description: str = Field(min_length=1)
    max_age_minutes: int | None = Field(default=None, ge=1)


class InjuryFreshness(StrictModel):
    max_age_minutes: int = Field(ge=1)
    validation_status: Literal["unvalidated_operational_default"]
    note: str = Field(min_length=1)


class DefenderReturnPolicy(StrictModel):
    markets: tuple[Market, ...]
    flagged_research_when_secondary_unclear: bool
    required_facts: tuple[str, ...]


class StreakPolicy(StrictModel):
    markets: tuple[Market, ...]
    independent_evidence: tuple[str, ...]
    raises_projection_from_streak: Literal[False]
    raises_confidence_from_streak: Literal[False]


class ReboundPolicy(StrictModel):
    markets: tuple[Market, ...]
    minimum_consecutive_misses: int = Field(ge=3)
    required_stability: tuple[str, ...]
    exclusion_causes: tuple[str, ...]


class AnglePolicies(StrictModel):
    defender_return: DefenderReturnPolicy
    no_streak_chasing: StreakPolicy
    underperformance_rebound: ReboundPolicy


class RuleCatalog(StrictModel):
    schema_version: int = Field(ge=1)
    rules_version: str = Field(min_length=1)
    model_version: str
    markets: tuple[Market, ...]
    numeric_hypothesis_adjustments_enabled: bool
    hypothesis_magnitudes: dict[str, None]
    future_context_models: tuple[str, ...]
    injury_freshness: InjuryFreshness
    angle_policies: AnglePolicies
    rules: tuple[RuleSpec, ...]
    source_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def check_catalog(self) -> "RuleCatalog":
        if self.model_version != "none":
            raise ValueError("model_version must be none until a projection model exists")
        if self.numeric_hypothesis_adjustments_enabled:
            raise ValueError("numeric hypothesis adjustments must stay disabled")
        if set(self.hypothesis_magnitudes) != {
            "defender_return",
            "no_streak_chasing",
            "underperformance_rebound",
        }:
            raise ValueError("hypothesis magnitudes must be unset for the three angle policies")
        if any(value is not None for value in self.hypothesis_magnitudes.values()):
            raise ValueError("hypothesis magnitudes must be unset")
        if set(self.future_context_models) != FUTURE_CONTEXT_MODELS:
            raise ValueError("pace, usage, and rest are future context models")
        if set(self.markets) != set(Market):
            raise ValueError("markets must be points, assists, and rebounds")
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        if set(ids) != REQUIRED_RULE_IDS:
            raise ValueError("rule catalog does not match the required input gates")
        precedences = [rule.precedence for rule in self.rules]
        if len(precedences) != len(set(precedences)):
            raise ValueError("rule precedence values must be unique")
        stale = next(rule for rule in self.rules if rule.id == "stale_injury_report")
        if stale.max_age_minutes != self.injury_freshness.max_age_minutes:
            raise ValueError("stale injury gate must match the configured freshness window")
        for rule in self.rules:
            if rule.id != "stale_injury_report" and rule.max_age_minutes is not None:
                raise ValueError("only stale_injury_report may set max_age_minutes")
        defender = self.angle_policies.defender_return
        if set(defender.markets) != {Market.POINTS}:
            raise ValueError("defender return applies to points only")
        if set(defender.required_facts) != DEFENDER_FACTS:
            raise ValueError("defender return required facts are incomplete")
        streak = self.angle_policies.no_streak_chasing
        rebound = self.angle_policies.underperformance_rebound
        if set(streak.markets) != set(Market) or set(rebound.markets) != set(Market):
            raise ValueError("streak and rebound policies apply to all three markets")
        if set(streak.independent_evidence) != STREAK_EVIDENCE:
            raise ValueError("streak policy is missing an independent evidence category")
        if rebound.minimum_consecutive_misses != 3:
            raise ValueError("underperformance rebound requires three consecutive misses")
        if set(rebound.required_stability) != REBOUND_STABILITY:
            raise ValueError("underperformance stability requirements are incomplete")
        if set(rebound.exclusion_causes) != REBOUND_EXCLUSIONS:
            raise ValueError("underperformance exclusion causes are incomplete")
        return self

    def ordered(self) -> tuple[RuleSpec, ...]:
        return tuple(sorted(self.rules, key=lambda rule: (rule.precedence, rule.id)))


class FoundationReport(StrictModel):
    state: AnalysisState
    reasons: tuple[str, ...] = ()
    rules_version: str | None = None
    model_version: str | None = None


def load_rules(path: Path) -> RuleCatalog:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("rules file must be a JSON object")
    payload = dict(payload)
    payload["source_sha256"] = sha256(raw).hexdigest()
    try:
        return RuleCatalog.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def load_foundation(root: Path) -> FoundationReport:
    reasons: list[str] = []
    rules_path = root / "config" / "custom-rules.json"
    try:
        rules = load_rules(rules_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return FoundationReport(state=AnalysisState.UNAVAILABLE, reasons=("invalid_rules",), model_version=None)

    provider_status, _providers = load_provider_file(root / "config" / "providers.example.yaml")
    schedule_status, schedules = load_schedule_file(root / "config" / "schedules.example.yaml")
    env_status, env_values = load_env_example(root / ".env.example")
    logs_status, _logs = load_nba_api_log_settings(root / "config" / "nba-api-logs.example.yaml")
    api_schedule_status, api_schedule = load_nba_api_schedule_settings(root / "config" / "nba-api-schedule.example.yaml")
    team_map_status = ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration")
    if api_schedule is not None:
        team_map_path = Path(api_schedule.team_map)
        if not team_map_path.is_absolute():
            team_map_path = root / team_map_path
        team_map_status, _team_map = load_team_abbreviations(team_map_path)
    odds_status, odds_settings = load_odds_api_settings(root / "config" / "odds-api.example.yaml")
    odds_team_status = ConfigStatus(state=AnalysisState.UNAVAILABLE, reason="invalid_configuration")
    if odds_settings is not None:
        odds_team_path = Path(odds_settings.team_names)
        if not odds_team_path.is_absolute():
            odds_team_path = root / odds_team_path
        odds_team_status, _odds_teams = load_odds_api_teams(odds_team_path)
    for status in (
        provider_status,
        schedule_status,
        env_status,
        logs_status,
        api_schedule_status,
        team_map_status,
        odds_status,
        odds_team_status,
    ):
        if status.state is not AnalysisState.AVAILABLE and status.reason is not None:
            reasons.append(status.reason)
    if schedules is not None:
        rule_ids = {rule.id for rule in rules.rules}
        for job in schedules.jobs:
            if job.injury_freshness_rule not in rule_ids:
                reasons.append("unknown_rule")
            if set(item.value for item in job.markets) != set(item.value for item in rules.markets):
                reasons.append("market_scope_mismatch")
    if env_status.state is AnalysisState.AVAILABLE:
        if env_values.get("NBA_RULES_VERSION") != rules.rules_version:
            reasons.append("rules_version_mismatch")
        if env_values.get("NBA_MODEL_VERSION") != rules.model_version:
            reasons.append("model_version_mismatch")
    unique = tuple(dict.fromkeys(reasons))
    state = AnalysisState.AVAILABLE if not unique else AnalysisState.UNAVAILABLE
    return FoundationReport(
        state=state,
        reasons=unique,
        rules_version=rules.rules_version,
        model_version=rules.model_version,
    )
