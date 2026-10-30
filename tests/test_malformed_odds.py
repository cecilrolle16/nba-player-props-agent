from contracts.common import AnalysisState
from contracts.market import quote_problems
from rules.engine import evaluate
from support import catalog, load_subject


def test_malformed_odds_are_unavailable():
    result = evaluate(catalog(), load_subject("malformed_odds.json"))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "malformed_odds"
    assert result.model_version == "none"
    assert "mean" not in result.model_dump()


def test_missing_side_and_even_token_are_malformed():
    subject = load_subject("valid_points.json")
    quote = subject.snapshot.model_copy(update={"side": None, "american_odds": "EVEN"})
    result = evaluate(catalog(), subject.model_copy(update={"snapshot": quote}))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "malformed_odds"
    assert "side" in quote_problems(quote, "points")
    assert "american_odds" in quote_problems(quote, "points")


def test_short_price_is_malformed():
    subject = load_subject("valid_points.json")
    quote = subject.snapshot.model_copy(update={"american_odds": "+99"})
    result = evaluate(catalog(), subject.model_copy(update={"snapshot": quote}))
    assert result.reason == "malformed_odds"
