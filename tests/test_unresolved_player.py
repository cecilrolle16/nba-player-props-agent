from contracts.common import AnalysisState
from rules.engine import evaluate
from support import catalog, load_subject


def test_null_player_id_is_unavailable():
    result = evaluate(catalog(), load_subject("unresolved_player.json"))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "unresolved_player_id"
    assert result.canonical_player_id is None


def test_player_id_mismatch_is_unavailable():
    subject = load_subject("valid_points.json")
    quote = subject.snapshot.model_copy(update={"canonical_player_id": "player:synthetic-wing-b"})
    result = evaluate(catalog(), subject.model_copy(update={"snapshot": quote}))
    assert result.state is AnalysisState.UNAVAILABLE
    assert result.reason == "player_id_mismatch"


def test_malformed_slug_is_unresolved():
    subject = load_subject("valid_points.json")
    result = evaluate(catalog(), subject.model_copy(update={"canonical_player_id": "Synthetic Guard A"}))
    assert result.reason == "unresolved_player_id"
