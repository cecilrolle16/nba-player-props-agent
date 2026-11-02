"""Baseline mean formulas. These do not build an outcome distribution.

Unshrunk rate = historical_stat_total / historical_minutes

Shrunk rate = (historical_stat_total + prior_rate * prior_minutes)
              / (historical_minutes + prior_minutes)

mean = expected_minutes * selected_rate

The prior is an input. This module does not choose one. Expected minutes
are an input. Historical average minutes are not a substitute.
"""


def unshrunk_rate(stat_total: float, historical_minutes: float) -> float:
    return stat_total / historical_minutes


def shrunk_rate(
    stat_total: float,
    historical_minutes: float,
    prior_rate: float,
    prior_minutes: float,
) -> float:
    return (stat_total + prior_rate * prior_minutes) / (historical_minutes + prior_minutes)


def baseline_mean(expected_minutes: float, selected_rate: float) -> float:
    return expected_minutes * selected_rate
