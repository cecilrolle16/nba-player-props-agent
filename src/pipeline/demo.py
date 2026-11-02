"""One offline demo of the implemented stages.

The inputs are synthetic and labeled as such. This module does not read
environment files, API credentials, or the network. The research decision
is the final research outcome. The legacy pass or abstain record is only
an input-eligibility note.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.analysis import AnalysisSubject
from contracts.common import GameStatus, SourceId
from contracts.configuration import ResearchDecisionSettings
from contracts.distribution import BASELINE_VERSION
from contracts.features import FEATURE_VERSION
from contracts.integer_residual import RESIDUAL_VERSION, ResidualTarget, ResidualTrainingRow
from contracts.pipeline import PipelineInput, PipelineReport
from contracts.player_log import PlayerGameLog
from contracts.price import PRICE_VERSION
from contracts.research import POLICY_VERSION
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from projection.residual import fit_integer_residual
from pipeline.run import run_pipeline
from rules.catalog import load_rules

ROOT = Path(__file__).resolve().parents[2]
JSON_MARKER = "--- demo-json ---"
PLAYER = "player:synthetic-guard-a"
TARGET_GAME = "game:synthetic-2026-11-01-home-away"
HISTORY_POINTS = 30
HISTORY_ASSISTS = 6
HISTORY_REBOUNDS = 8
HISTORY_MINUTES = 30.0
HISTORY_COUNT = 5
TRAINING_COUNT = 8
KNOWN = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
DISTRIBUTION_AVAILABLE_AT = datetime(2026, 11, 1, 22, 50, tzinfo=timezone.utc)

CANDIDATE_POLICY = ResearchDecisionSettings(
    version=1,
    minimum_ev=0.01,
    minimum_probability_edge=0.01,
    edge_benchmark="raw_implied",
    configuration_label="synthetic_test",
    thresholds_validated=False,
    recommendations_enabled=False,
    hypothesis_adjustments_enabled=False,
)
NO_BET_POLICY = CANDIDATE_POLICY.model_copy(update={"minimum_ev": 1.0})

ASSUMPTIONS = (
    "Every player, team, book, log, and quote in this demo is synthetic.",
    "Stage origin observed means the stage calculated the number. It does not mean the input is a real NBA game.",
    "Expected minutes are the single number on the synthetic minutes record. Minutes uncertainty is not modeled.",
    "The feature window is the shipped unvalidated default: 10 games, 30 days, minimum 5.",
    "Shrinkage is off. No prior rate is supplied.",
    "The histogram uses fixed minutes with tolerance zero. That is an exact sample condition, not a distribution over minutes.",
    "The histogram is experimental and uncalibrated. Exact mean preservation is not guaranteed.",
    "Synthetic training rows place a zero residual on the calculated baseline mean. The histogram code places that mass. The probabilities are not typed in.",
    "The required edge benchmark is raw implied probability. Raw implied probability contains vig. No-vig is not used and is not a true probability.",
    "minimum_ev and minimum_probability_edge are synthetic_test labels. They are not an operational cutoff and not a claim of profit.",
    "The three custom-rule audits stay on. Numeric hypothesis adjustments stay disabled.",
    "A research candidate is not a live recommendation. Stake and bankroll stay null.",
)

REMAINING_GAPS = (
    "Real-data evaluation is not part of this demo.",
    "Minutes uncertainty is not modeled.",
    "Team-context and injury feeds are not connected. The injury row in the fixture is synthetic input evidence only.",
    "Settlement grading is not implemented.",
    "AWS deployment is not implemented.",
    "No provider is called. The shipped research thresholds stay unset.",
)


def run_demo() -> dict[str, object]:
    """Run the three labeled scenarios and return a JSON-ready document."""

    catalog = load_rules(ROOT / "config" / "custom-rules.json")
    eligible = _bundle(subject=_subject("valid_points.json"), policy=CANDIDATE_POLICY)
    distribution = _fit_residual(eligible, catalog)
    candidate = run_pipeline(
        eligible.model_copy(
            update={
                "residual_distribution": distribution,
                "distribution_available_at": DISTRIBUTION_AVAILABLE_AT,
                "research_policy": CANDIDATE_POLICY,
            }
        ),
        catalog,
    )
    no_bet = run_pipeline(
        eligible.model_copy(
            update={
                "residual_distribution": distribution,
                "distribution_available_at": DISTRIBUTION_AVAILABLE_AT,
                "research_policy": NO_BET_POLICY,
            }
        ),
        catalog,
    )
    blocked_subject = _subject("valid_points.json").model_copy(update={"minutes": None})
    unavailable = run_pipeline(
        _bundle(subject=blocked_subject, policy=CANDIDATE_POLICY).model_copy(
            update={
                "residual_distribution": distribution,
                "distribution_available_at": DISTRIBUTION_AVAILABLE_AT,
            }
        ),
        catalog,
    )
    document = {
        "demo": "synthetic-offline-framework",
        "data_label": "synthetic",
        "credentials_read": False,
        "live_recommendation": False,
        "stake": None,
        "bankroll_sizing": None,
        "hypothesis_adjustments_enabled": False,
        "final_outcome_record": "research_decision",
        "legacy_record_role": (
            "Input eligibility only. pass means the input gates accepted the quote. "
            "abstain means they did not. Neither value is a research outcome or a bet."
        ),
        "versions": {
            "rules_version": catalog.rules_version,
            "rules_model_version": catalog.model_version,
            "feature_version": FEATURE_VERSION,
            "baseline_version": BASELINE_VERSION,
            "distribution_version": RESIDUAL_VERSION,
            "price_version": PRICE_VERSION,
            "research_policy_version": POLICY_VERSION,
        },
        "assumptions": list(ASSUMPTIONS),
        "remaining_gaps": list(REMAINING_GAPS),
        "scenarios": [
            _scenario("research_candidate", candidate, distribution_id=distribution.distribution_id, used=True),
            _scenario("no_bet", no_bet, distribution_id=distribution.distribution_id, used=True),
            _scenario(
                "missing_essential_evidence",
                unavailable,
                distribution_id=distribution.distribution_id,
                used=False,
            ),
        ],
    }
    _require_outcomes(document)
    return document


def render(document: dict[str, object]) -> str:
    """Readable summary. The research outcome is named before the eligibility record."""

    lines = [
        "Synthetic offline demo",
        "Data label: synthetic",
        "Credentials read: no",
        "Live recommendation: false",
        "Stake: null",
        "Bankroll sizing: null",
        "Numeric hypothesis adjustments: disabled",
        "Final research outcome record: research_decision",
        "",
        "Versions:",
    ]
    versions = document["versions"]
    assert isinstance(versions, dict)
    for key, value in versions.items():
        lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append("Assumptions:")
    assumptions = document["assumptions"]
    assert isinstance(assumptions, list)
    lines.extend(f"  - {item}" for item in assumptions)
    lines.append("")
    lines.append("Remaining gaps:")
    gaps = document["remaining_gaps"]
    assert isinstance(gaps, list)
    lines.extend(f"  - {item}" for item in gaps)
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        outcome = scenario["final_research_outcome"]
        eligibility = scenario["input_eligibility_record"]
        assert isinstance(outcome, dict)
        assert isinstance(eligibility, dict)
        lines.extend(
            [
                "",
                f"Scenario: {scenario['scenario_id']}",
                f"Final research outcome: {outcome['result']}",
                f"Research reason: {outcome['reason']}",
                f"Data quality: {outcome['data_quality']}",
                f"Calibration: {outcome['calibration_status']}",
                f"Hit probability reference: {outcome['hit_probability_reference']}",
                f"Configuration: {outcome['configuration_label']} "
                f"benchmark={outcome['edge_benchmark']} "
                f"minimum_ev={outcome['minimum_ev']} "
                f"minimum_probability_edge={outcome['minimum_probability_edge']}",
                "Rule audits:",
            ]
        )
        audits = scenario["rule_audits"]
        assert isinstance(audits, list)
        for audit in audits:
            assert isinstance(audit, dict)
            lines.append(
                f"  - {audit['policy_id']}: {audit['status']} / {audit['reason']}; "
                f"adjustment=null; validation={audit['validation_status']}"
            )
        sources = scenario["source_references"]
        assert isinstance(sources, dict)
        lines.append(
            "Sources: "
            f"audit={sources['audit_id'][:12]} "
            f"quote={sources['quote_snapshot_id']} "
            f"projection={sources['canonical_projection_id']} "
            f"price={sources['price_id']}"
        )
        lines.append(
            "Input eligibility record: "
            f"{eligibility['result']} / {eligibility['reason']} "
            "(not a research outcome and not a bet)"
        )
    return "\n".join(lines)


def main() -> int:
    document = run_demo()
    print(render(document))
    print(JSON_MARKER)
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


def _require_outcomes(document: dict[str, object]) -> None:
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    found = {item["scenario_id"]: item for item in scenarios if isinstance(item, dict)}
    candidate = found["research_candidate"]
    no_bet = found["no_bet"]
    unavailable = found["missing_essential_evidence"]
    if candidate["final_research_outcome"]["result"] != "research_candidate":
        raise RuntimeError("the synthetic candidate path did not clear the labeled test policy")
    if no_bet["final_research_outcome"]["reason"] != "ev_below_minimum":
        raise RuntimeError("the no-bet path did not fail the labeled EV minimum")
    if unavailable["final_research_outcome"]["reason"] != "missing_essential_evidence":
        raise RuntimeError("the unavailable path was not missing essential evidence")
    for item in (candidate, no_bet, unavailable):
        outcome = item["final_research_outcome"]
        eligibility = item["input_eligibility_record"]
        if outcome["live_recommendation"] is not False or outcome["stake"] is not None:
            raise RuntimeError("a research outcome carried a recommendation or a stake")
        if "ev_per_unit" in outcome or "probability_edge" in outcome or "edge" in outcome:
            raise RuntimeError("the research outcome copied a price number")
        if eligibility["not_a_research_outcome"] is not True:
            raise RuntimeError("the legacy record was presented as a research outcome")


def _scenario(
    scenario_id: str,
    report: PipelineReport,
    *,
    distribution_id: str,
    used: bool,
) -> dict[str, object]:
    research = report.research_decision
    failed = [gate for gate in research.gates if gate.status == "failed"]
    reason = failed[0].reason if failed else "all_research_gates_passed"
    price = report.price
    comparison = report.comparison
    features = report.features
    projection = report.projection
    return {
        "scenario_id": scenario_id,
        "data_label": "synthetic",
        "final_research_outcome": {
            "record": "research_decision",
            "result": research.result,
            "reason": reason,
            "decision_id": research.decision_id,
            "policy_version": research.policy_version,
            "data_quality": research.data_quality.value,
            "calibration_status": research.calibration_status.value,
            "hit_probability_reference": research.hit_probability_reference,
            "origin": research.origin,
            "origin_note": (
                "observed means calculated from the supplied inputs. Those inputs are synthetic."
                if research.origin == "observed"
                else "synthetic means the comparison stage was an injected synthetic value."
            ),
            "experimental": research.experimental,
            "live_recommendation": research.live_recommendation,
            "stake": research.stake,
            "bankroll_sizing": research.bankroll_sizing,
            "thresholds_validated": research.thresholds_validated,
            "recommendations_enabled": research.recommendations_enabled,
            "hypothesis_adjustments_enabled": research.hypothesis_adjustments_enabled,
            "configuration_label": research.configuration_label,
            "edge_benchmark": research.edge_benchmark,
            "priced_edge_benchmark": research.priced_edge_benchmark,
            "minimum_ev": research.minimum_ev,
            "minimum_probability_edge": research.minimum_probability_edge,
            "price_id": research.price_id,
            "comparison_id": research.comparison_id,
            "canonical_projection_id": research.canonical_projection_id,
            "audit_id": research.audit_id,
            "gates": [
                {"gate_id": gate.gate_id, "status": gate.status, "reason": gate.reason}
                for gate in research.gates
            ],
        },
        "input_eligibility_record": {
            "record": "legacy_decision",
            "role": "input_eligibility_only",
            "not_a_research_outcome": True,
            "result": report.decision.result,
            "reason": report.decision.reason,
            "live_recommendation": report.decision.live_recommendation,
            "stake": report.decision.stake,
            "edge": report.decision.edge,
            "ev_per_unit": report.decision.ev_per_unit,
        },
        "rule_audits": [
            {
                "policy_id": angle.policy_id,
                "status": angle.status,
                "reason": angle.reason,
                "adjustment_amount": angle.adjustment_amount,
                "validation_status": angle.validation_status,
                "evidence_snapshot_ids": list(angle.evidence_snapshot_ids),
            }
            for angle in report.validation.audit.angles
        ],
        "source_references": {
            "audit_id": report.validation.audit.audit_id,
            "rules_fingerprint": report.validation.audit.rules_fingerprint,
            "schedule_snapshot_id": features.schedule_snapshot_id,
            "log_snapshot_ids": list(features.log_snapshot_ids),
            "quote_snapshot_id": features.quote_snapshot_id,
            "evidence_snapshot_ids": list(features.evidence_snapshot_ids),
            "feature_id": features.feature_id,
            "canonical_projection_id": projection.canonical_projection_id,
            "projection_source_ids": [
                {"provider_id": source.provider_id, "native_id": source.native_id}
                for source in projection.source_ids
            ],
            "distribution_id": distribution_id if used else None,
            "comparison_id": comparison.comparison_id,
            "odds_snapshot_id": comparison.odds_snapshot_id,
            "price_id": None if price is None else price.price_id,
        },
        "stages": {
            "features": {
                "stage_state": features.stage_state.value,
                "feature_version": features.feature_version,
                "sample_size": features.sample_size,
                "total_points": features.total_points,
                "total_minutes": features.total_minutes,
                "insufficient_history": features.insufficient_history,
                "origin": features.origin.value,
                "calibration_status": features.calibration_status.value,
                "data_label": "synthetic",
            },
            "baseline": {
                "stage_state": projection.stage_state.value,
                "model_version": projection.model_version,
                "mean": projection.mean,
                "expected_minutes": projection.expected_minutes,
                "minutes_uncertainty": projection.minutes_uncertainty,
                "baseline_label": projection.baseline_label,
                "shrinkage_enabled": projection.shrinkage_enabled,
                "calibration_status": projection.calibration_status.value,
                "missingness": projection.missingness,
            },
            "distribution": {
                "used": used,
                "distribution_id": distribution_id if used else None,
                "model_version": comparison.model_version if used else None,
                "reason_not_used": None if used else report.validation.reason,
            },
            "probabilities": {
                "stage_state": comparison.stage_state.value,
                "p_win": comparison.p_win,
                "p_loss": comparison.p_loss,
                "p_push": comparison.p_push,
                "calibration_status": comparison.calibration_status.value,
                "experimental": comparison.experimental,
                "limitations": list(comparison.limitations),
                "implied_probability": comparison.implied_probability,
            },
            "price": None
            if price is None
            else {
                "price_id": price.price_id,
                "price_version": price.model_version,
                "status": price.status,
                "edge_benchmark": price.edge_benchmark,
                "ev_per_unit": price.ev_per_unit,
                "probability_edge": price.probability_edge,
                "raw_implied_probability": price.raw_implied_probability,
                "no_vig_probability": price.no_vig_probability,
                "mean_minus_line": price.mean_minus_line,
                "calibration_status": price.calibration_status.value,
                "experimental": price.experimental,
                "live_recommendation": price.live_recommendation,
                "stake": price.stake,
                "limitations": list(price.limitations),
            },
        },
        "calibration_status": {
            "data_quality": research.data_quality.value,
            "features": features.calibration_status.value,
            "baseline": projection.calibration_status.value,
            "comparison": comparison.calibration_status.value,
            "price": None if price is None else price.calibration_status.value,
            "research_decision": research.calibration_status.value,
        },
    }


def _fit_residual(bundle: PipelineInput, catalog):
    preview = run_pipeline(bundle, catalog)
    projection = preview.projection
    if projection.mean is None or projection.expected_minutes is None or projection.model_version != BASELINE_VERSION:
        raise RuntimeError(projection.missingness or "baseline_not_calculated")
    if abs(projection.mean - round(projection.mean)) > 1e-9:
        raise RuntimeError("this synthetic demo expects the calculated baseline mean to land on an integer")
    observed = int(round(projection.mean))
    cutoff = bundle.subject.as_of
    target = ResidualTarget(
        canonical_player_id=PLAYER,
        canonical_game_id=TARGET_GAME,
        market=bundle.subject.market,
        model_version=BASELINE_VERSION,
        baseline_mean=projection.mean,
        expected_minutes=projection.expected_minutes,
        decision_cutoff=cutoff,
        source_ids=projection.source_ids,
    )
    rows = []
    for index in range(1, TRAINING_COUNT + 1):
        decision_cutoff = datetime(2026, 10, index, 18, 0, tzinfo=timezone.utc)
        rows.append(
            ResidualTrainingRow(
                canonical_player_id=PLAYER,
                canonical_game_id=f"game:synthetic-train-{index:02d}",
                market=bundle.subject.market,
                model_version=BASELINE_VERSION,
                predicted_mean=projection.mean,
                expected_minutes=projection.expected_minutes,
                decision_cutoff=decision_cutoff,
                prediction_available_at=decision_cutoff - timedelta(hours=1),
                observed_stat=observed,
                outcome_available_at=decision_cutoff + timedelta(hours=1),
                played=True,
                split="train",
                source_ids=(SourceId(provider_id="synthetic-training", native_id=f"train-{index:02d}"),),
            )
        )
    fit = fit_integer_residual(target, tuple(rows))
    if fit.distribution is None:
        raise RuntimeError(fit.reason or "residual_not_calculated")
    return fit.distribution


def _bundle(*, subject: AnalysisSubject, policy: ResearchDecisionSettings) -> PipelineInput:
    payload = json.loads((ROOT / "tests" / "fixtures" / "synthetic_framework.json").read_text(encoding="utf-8"))
    logs = []
    games = []
    snapshot_ids = []
    for index in range(1, HISTORY_COUNT + 1):
        game_id = f"game:synthetic-hist-{index:02d}"
        snapshot_id = f"snapshot:synthetic-hist-{index:02d}-log"
        logs.append(
            PlayerGameLog(
                canonical_player_id=PLAYER,
                canonical_game_id=game_id,
                canonical_team_id="team:synthetic-home",
                points=HISTORY_POINTS,
                assists=HISTORY_ASSISTS,
                rebounds=HISTORY_REBOUNDS,
                minutes=HISTORY_MINUTES,
                available_at=KNOWN,
                source_ids=(SourceId(provider_id="synthetic-logs", native_id=game_id),),
            )
        )
        games.append(
            ScheduleGame(
                canonical_game_id=game_id,
                snapshot_id=f"snapshot:synthetic-hist-{index:02d}-schedule",
                season="2026-27",
                home_team_id="team:synthetic-home",
                away_team_id="team:synthetic-away",
                status=GameStatus.FINAL,
                tip_at=datetime(2026, 10, 8 + index * 3, tzinfo=timezone.utc),
                available_at=KNOWN,
                source_ids=(SourceId(provider_id="synthetic-schedule", native_id=game_id),),
            )
        )
        snapshot_ids.append(snapshot_id)
    return PipelineInput(
        subject=subject,
        schedule=ScheduleGame.model_validate(payload["schedule"]),
        roster=RosterEntry.model_validate(payload["roster"]),
        player_logs=tuple(logs),
        log_snapshot_ids=tuple(snapshot_ids),
        history_games=tuple(games),
        research_policy=policy,
    )


def _subject(name: str) -> AnalysisSubject:
    payload = json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))
    return AnalysisSubject.model_validate(payload)


if __name__ == "__main__":
    raise SystemExit(main())
