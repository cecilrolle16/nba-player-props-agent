"""Baseline mean from expected minutes and a historical per-minute rate.

The rate is the included stat total divided by included minutes, optionally
shrunk toward an explicit prior. Expected minutes are never copied from the
historical average. The result is a mean, not a distribution.
"""

from datetime import datetime

from contracts.common import Market, SourceId
from contracts.configuration import BaselineProjectionSettings
from contracts.distribution import BASELINE_VERSION
from contracts.features import FeatureSnapshot
from contracts.projection import ExpectedMinutesInput, RatePrior
from contracts.stages import CalibrationStatus, StageState, ValueOrigin
from projection.formulas import baseline_mean, shrunk_rate, unshrunk_rate

DEFAULT_BASELINE = BaselineProjectionSettings(
    version=1,
    shrinkage_enabled=False,
    coefficients_validated=False,
)


def project_baseline(
    *,
    features: FeatureSnapshot,
    minutes: ExpectedMinutesInput | None,
    prior: RatePrior | None,
    shrinkage_enabled: bool,
    cutoff: datetime,
) -> dict[str, object]:
    """Return projection fields. A refusal has a missingness reason and no mean."""

    reason = _refusal_reason(
        features=features,
        minutes=minutes,
        prior=prior,
        shrinkage_enabled=shrinkage_enabled,
        cutoff=cutoff,
    )
    if reason is not None:
        return _refused(reason, shrinkage_enabled)
    assert minutes is not None
    assert minutes.expected_minutes is not None
    total = _stat_total(features, features.market)
    assert total is not None and features.total_minutes is not None
    historical = unshrunk_rate(total, features.total_minutes)
    if shrinkage_enabled:
        assert prior is not None
        selected = shrunk_rate(total, features.total_minutes, prior.prior_rate, prior.prior_minutes)
        label = "shrunk"
        prior_rate = prior.prior_rate
        prior_minutes = prior.prior_minutes
        prior_snapshot_id = prior.snapshot_id
    else:
        selected = historical
        label = "unshrunk"
        prior_rate = None
        prior_minutes = None
        prior_snapshot_id = None
    sources = _sources(minutes, prior if shrinkage_enabled else None)
    return {
        "stage_state": StageState.CALCULATED,
        "origin": ValueOrigin.OBSERVED,
        "calibration_status": CalibrationStatus.NOT_APPLICABLE,
        "model_version": BASELINE_VERSION,
        "projection_version": BASELINE_VERSION,
        "mean": baseline_mean(minutes.expected_minutes, selected),
        "outcomes": (),
        "projected_at": cutoff,
        "available_at": cutoff,
        "source_ids": sources,
        "expected_minutes": minutes.expected_minutes,
        "minutes_uncertainty": minutes.minutes_uncertainty,
        "minutes_snapshot_id": minutes.snapshot_id,
        "historical_minutes": features.total_minutes,
        "historical_stat_total": total,
        "historical_rate": historical,
        "selected_rate": selected,
        "shrinkage_enabled": shrinkage_enabled,
        "baseline_label": label,
        "prior_rate": prior_rate,
        "prior_minutes": prior_minutes,
        "prior_snapshot_id": prior_snapshot_id,
        "coefficients_validated": False,
        "missingness": None,
        "canonical_projection_id": f"projection:baseline-{features.market.value}",
    }


def _refusal_reason(
    *,
    features: FeatureSnapshot,
    minutes: ExpectedMinutesInput | None,
    prior: RatePrior | None,
    shrinkage_enabled: bool,
    cutoff: datetime,
) -> str | None:
    if features.stage_state is not StageState.CALCULATED:
        return "feature_not_ready"
    if cutoff >= features.schedule_tip_at:
        return "after_tip"
    if features.as_of > cutoff or features.available_at > cutoff:
        return "future_evidence"
    if features.as_of != cutoff:
        return "cutoff_mismatch"
    if minutes is None:
        return "missing_expected_minutes"
    if (
        minutes.canonical_player_id != features.canonical_player_id
        or minutes.canonical_game_id != features.canonical_game_id
        or minutes.market != features.market
    ):
        return "identity_mismatch"
    if minutes.available_at is not None and minutes.available_at > cutoff:
        return "future_evidence"
    if (
        not minutes.known
        or minutes.expected_minutes is None
        or minutes.expected_minutes <= 0
        or minutes.snapshot_id is None
        or minutes.available_at is None
    ):
        return "invalid_expected_minutes"
    if prior is not None:
        if (
            prior.canonical_player_id != features.canonical_player_id
            or prior.canonical_game_id != features.canonical_game_id
            or prior.market != features.market
        ):
            return "identity_mismatch"
        if prior.available_at > cutoff:
            return "future_evidence"
    if shrinkage_enabled and prior is None:
        return "missing_prior"
    if features.insufficient_history or not features.sample_size or features.total_minutes is None:
        return "insufficient_history"
    if _stat_total(features, features.market) is None:
        return "insufficient_history"
    return None


def _stat_total(features: FeatureSnapshot, market: Market) -> float | None:
    if market is Market.POINTS:
        return features.total_points
    if market is Market.ASSISTS:
        return features.total_assists
    return features.total_rebounds


def _sources(minutes: ExpectedMinutesInput, prior: RatePrior | None) -> tuple[SourceId, ...]:
    found: list[SourceId] = []
    if minutes.snapshot_id is not None:
        found.append(SourceId(provider_id="minutes-evidence", native_id=minutes.snapshot_id))
    found.extend(minutes.source_ids)
    if prior is not None:
        found.extend(prior.source_ids)
    return tuple(found)


def _refused(reason: str, shrinkage_enabled: bool) -> dict[str, object]:
    return {
        "canonical_projection_id": f"projection:baseline-{reason.replace('_', '-')}",
        "stage_state": StageState.UNAVAILABLE,
        "origin": ValueOrigin.OBSERVED,
        "calibration_status": CalibrationStatus.NOT_APPLICABLE,
        "model_version": BASELINE_VERSION,
        "projection_version": BASELINE_VERSION,
        "mean": None,
        "outcomes": (),
        "projected_at": None,
        "available_at": None,
        "source_ids": (SourceId(provider_id="baseline-projection", native_id=reason),),
        "expected_minutes": None,
        "minutes_uncertainty": None,
        "minutes_snapshot_id": None,
        "historical_minutes": None,
        "historical_stat_total": None,
        "historical_rate": None,
        "selected_rate": None,
        "shrinkage_enabled": shrinkage_enabled,
        "baseline_label": None,
        "prior_rate": None,
        "prior_minutes": None,
        "prior_snapshot_id": None,
        "coefficients_validated": False,
        "missingness": reason,
    }
