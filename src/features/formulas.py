"""Historical box-score formulas.

These describe games that were already included. They do not project a
future stat, a win probability, an edge, or an expected value.

For an included sample of size n:

- total_stat is the sum of the observed stat
- average_stat = total_stat / n
- stat_per_minute = total_stat / total_minutes
- sample variability, when n >= 2, is the sample standard deviation

    sqrt( sum((x_i - average_stat)^2) / (n - 1) )

The divisor is n - 1 (Bessel's correction), not n. When n < 2 the
variability is null. When n is 0 every total, average, rate, and
variability is null. A missing stat is not entered as zero.
"""

from math import sqrt


def sample_standard_deviation(values: tuple[float, ...]) -> float | None:
    count = len(values)
    if count < 2:
        return None
    average = sum(values) / count
    squared = sum((value - average) ** 2 for value in values)
    return sqrt(squared / (count - 1))


def box_score_summary(
    minutes: tuple[float, ...],
    points: tuple[float, ...],
    assists: tuple[float, ...],
    rebounds: tuple[float, ...],
) -> dict[str, float | int | None]:
    count = len(minutes)
    if count == 0:
        return {
            "sample_size": 0,
            "total_minutes": None,
            "total_points": None,
            "total_assists": None,
            "total_rebounds": None,
            "average_minutes": None,
            "average_points": None,
            "average_assists": None,
            "average_rebounds": None,
            "points_per_minute": None,
            "assists_per_minute": None,
            "rebounds_per_minute": None,
            "points_variability": None,
            "assists_variability": None,
            "rebounds_variability": None,
        }
    total_minutes = sum(minutes)
    total_points = sum(points)
    total_assists = sum(assists)
    total_rebounds = sum(rebounds)
    return {
        "sample_size": count,
        "total_minutes": total_minutes,
        "total_points": total_points,
        "total_assists": total_assists,
        "total_rebounds": total_rebounds,
        "average_minutes": total_minutes / count,
        "average_points": total_points / count,
        "average_assists": total_assists / count,
        "average_rebounds": total_rebounds / count,
        "points_per_minute": total_points / total_minutes,
        "assists_per_minute": total_assists / total_minutes,
        "rebounds_per_minute": total_rebounds / total_minutes,
        "points_variability": sample_standard_deviation(points),
        "assists_variability": sample_standard_deviation(assists),
        "rebounds_variability": sample_standard_deviation(rebounds),
    }
