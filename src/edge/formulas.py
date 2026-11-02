"""American odds, expected value, and a proportional no-vig weight.

Positive American odds A convert as decimal = 1 + A/100.
Negative American odds A convert as decimal = 1 + 100/|A|.
Raw implied probability is 1/decimal. That number still contains vig.

EV per 1 unit stake is p_win * (decimal - 1) - p_loss.
A push returns the stake and adds nothing.

Conditional win probability is p_win / (p_win + p_loss).
It is undefined when that denominator is zero.

Proportional no-vig probability is this side's raw implied probability
divided by the sum of the two sides' raw implied probabilities.
It is not a true probability.
"""

from fractions import Fraction


def american_price(odds: str) -> tuple[Fraction, Fraction]:
    """Return decimal price and raw implied probability as exact fractions."""

    magnitude = int(odds[1:])
    if odds[0] == "+":
        profit = Fraction(magnitude, 100)
    else:
        profit = Fraction(100, magnitude)
    decimal = 1 + profit
    return decimal, 1 / decimal


def expected_value(p_win: float, p_loss: float, profit: Fraction) -> Fraction:
    """Stake-unit EV. The push term is omitted because its profit is zero."""

    return Fraction(str(p_win)) * profit - Fraction(str(p_loss))


def conditional_win_probability(p_win: float, p_loss: float) -> Fraction | None:
    """Win probability among non-pushes. None when every outcome is a push."""

    win = Fraction(str(p_win))
    loss = Fraction(str(p_loss))
    if win + loss == 0:
        return None
    return win / (win + loss)


def proportional_no_vig(side_implied: Fraction, other_implied: Fraction) -> Fraction:
    """Remove vig in proportion to the two raw implied probabilities."""

    return side_implied / (side_implied + other_implied)
