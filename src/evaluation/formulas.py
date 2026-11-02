"""Scores for a baseline mean. These do not score a probability or a bet.

`error` is prediction minus actual.

- sample count is the number of eligible pairs
- MAE is the mean of the absolute errors
- RMSE is the square root of the mean of the squared errors
- signed bias is the mean of the errors

An empty sample stores null scores, not zeros. The actual is never written
back onto the prediction.
"""

from math import sqrt


def mean_absolute_error(errors: tuple[float, ...]) -> float | None:
    if not errors:
        return None
    return sum(abs(error) for error in errors) / len(errors)


def root_mean_squared_error(errors: tuple[float, ...]) -> float | None:
    if not errors:
        return None
    return sqrt(sum(error * error for error in errors) / len(errors))


def signed_bias(errors: tuple[float, ...]) -> float | None:
    if not errors:
        return None
    return sum(errors) / len(errors)
