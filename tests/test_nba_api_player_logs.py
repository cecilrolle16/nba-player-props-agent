import copy
import json
import socket
from datetime import datetime, timezone

from contracts.common import AnalysisState
from contracts.configuration import load_nba_api_log_settings, validate_nba_api_log_settings, validate_providers
from ingestion.adapters.nba_api_logs import IdentityMap, NbaApiPlayerLogAdapter, normalize_playergamelog
from support import FIXTURES, ROOT


RETRIEVED = datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2025, 1, 20, 15, 0, tzinfo=timezone.utc)
IDENTITY = {
    "players": {"9990001": "player:synthetic-guard-a"},
    "teams": {"SYN": "team:synthetic-home"},
    "games": {
        "0099900001": "game:synthetic-2025-01-14",
        "0099900002": "game:synthetic-2025-01-10",
        "0099900003": "game:synthetic-2025-01-12",
        "0099900004": "game:synthetic-2025-01-11",
        "0099900005": "game:synthetic-2025-01-09",
        "0099900006": "game:synthetic-2025-01-08",
    },
}


def _rows():
    return json.loads((FIXTURES / "nba_api_playergamelog.json").read_text(encoding="utf-8"))


def _adapter(tmp_path, source, clock=None, sleeps=None, retries=2, interval=0):
    recorded = sleeps if sleeps is not None else []

    def sleeper(seconds):
        recorded.append(seconds)

    return NbaApiPlayerLogAdapter(
        timeout_seconds=5,
        max_retries=retries,
        min_interval_seconds=interval,
        cache_dir=tmp_path,
        row_source=source,
        clock=clock or (lambda: RETRIEVED),
        sleeper=sleeper,
        monotonic=lambda: 0.0,
    )


def test_normalization_keeps_retrieval_time_and_hides_provider_columns():
    payload = _rows()
    records, quarantined = normalize_playergamelog(
        [payload["played_clock_minutes"], payload["played_integer_minutes"]],
        nba_player_id="9990001",
        identity=IdentityMap.model_validate(IDENTITY),
        cutoff=CUTOFF,
        retrieved_at=RETRIEVED,
    )
    assert quarantined == ()
    assert [item.record.canonical_game_id for item in records] == [
        "game:synthetic-2025-01-10",
        "game:synthetic-2025-01-14",
    ]
    earlier, later = records
    assert earlier.record.minutes == 32.25
    assert earlier.record.points == 11
    assert earlier.record.assists == 8
    assert earlier.record.rebounds == 3
    assert later.record.minutes == 36.0
    assert later.record.points == 22
    assert later.record.assists == 7
    assert later.record.rebounds == 5
    assert earlier.record.available_at == RETRIEVED
    assert later.record.available_at == RETRIEVED
    assert earlier.snapshot_id == "snapshot:nba-api-0099900002"
    assert earlier.record.source_ids[0].provider_id == "nba-api"
    assert earlier.record.source_ids[0].native_id == "playergamelog:9990001:0099900002"
    assert "MATCHUP" not in earlier.record.model_dump()
    assert "MIN" not in earlier.record.model_dump()


def test_missing_fields_dnp_and_zero_minutes_stay_distinct(tmp_path):
    payload = _rows()
    seen = []

    def source(**_kwargs):
        seen.append(_kwargs)
        return [
            payload["zero_minutes"],
            payload["did_not_play"],
            payload["missing_points"],
            {**payload["played_integer_minutes"], "MIN": None},
            {key: value for key, value in payload["played_integer_minutes"].items() if key != "MIN"},
        ]

    result = _adapter(tmp_path, source).fetch_player_logs("9990001", "2024-25", CUTOFF, IDENTITY)
    assert result.state is AnalysisState.AVAILABLE
    assert result.point_in_time_limited is True
    assert "retrieval time" in result.point_in_time_note
    assert len(result.records) == 1
    assert result.records[0].record.minutes == 0.0
    assert result.records[0].record.points == 0
    reasons = [item.reason for item in result.quarantined]
    assert reasons == ["did_not_play", "missing_field", "did_not_play", "missing_field"]
    assert result.quarantined[1].detail == "PTS"
    assert result.quarantined[3].detail == "MIN"
    assert seen[0]["player_id"] == "9990001"
    assert seen[0]["season"] == "2024-25"


def test_unresolved_ids_are_quarantined_without_inventing_canonical_ids(tmp_path):
    payload = _rows()
    calls = []

    def source(**_kwargs):
        calls.append(1)
        return [payload["played_integer_minutes"]]

    unmapped = _adapter(tmp_path, source).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        {"players": {}, "teams": IDENTITY["teams"], "games": IDENTITY["games"]},
    )
    assert unmapped.state is AnalysisState.UNAVAILABLE
    assert unmapped.reason == "unresolved_player_id"
    assert unmapped.records == ()
    assert calls == []

    mismatched = dict(payload["played_integer_minutes"])
    mismatched["Player_ID"] = 42
    unknown_team = dict(payload["played_clock_minutes"])
    unknown_team["MATCHUP"] = "ZZZ vs. OPP"
    unknown_game = dict(payload["zero_minutes"])
    unknown_game["Game_ID"] = "0099900099"
    result = _adapter(tmp_path, lambda **_kwargs: [mismatched, unknown_team, unknown_game, payload["played_integer_minutes"]]).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        IDENTITY,
    )
    assert [item.reason for item in result.quarantined] == [
        "player_id_mismatch",
        "unresolved_team_id",
        "unresolved_game_id",
    ]
    assert [item.record.canonical_game_id for item in result.records] == ["game:synthetic-2025-01-14"]


def test_duplicate_rows_keep_one_record(tmp_path):
    payload = _rows()
    first = payload["played_integer_minutes"]
    second = dict(first)
    second["PTS"] = 99
    other_native = dict(payload["played_clock_minutes"])
    mapped = copy.deepcopy(IDENTITY)
    mapped["games"]["0099900002"] = "game:synthetic-2025-01-14"
    result = _adapter(tmp_path, lambda **_kwargs: [first, second, other_native]).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        mapped,
    )
    assert len(result.records) == 1
    assert result.records[0].record.points == 22
    assert [item.reason for item in result.quarantined] == ["duplicate_record", "duplicate_record"]


def test_cutoff_drops_same_day_and_later_games(tmp_path):
    payload = _rows()
    rows = [payload["played_clock_minutes"], payload["played_integer_minutes"], payload["zero_minutes"]]
    same_day = datetime(2025, 1, 14, 18, 0, tzinfo=timezone.utc)
    result = _adapter(tmp_path, lambda **_kwargs: rows).fetch_player_logs("9990001", "2024-25", same_day, IDENTITY)
    assert [item.record.canonical_game_id for item in result.records] == [
        "game:synthetic-2025-01-10",
        "game:synthetic-2025-01-12",
    ]
    assert [item.reason for item in result.quarantined] == ["cutoff_date_unresolved"]

    earlier_cutoff = datetime(2025, 1, 12, 0, 0, tzinfo=timezone.utc)
    later = _adapter(tmp_path / "later", lambda **_kwargs: rows).fetch_player_logs(
        "9990001",
        "2024-25",
        earlier_cutoff,
        IDENTITY,
    )
    assert [item.record.canonical_game_id for item in later.records] == ["game:synthetic-2025-01-10"]
    assert [item.reason for item in later.quarantined] == ["after_cutoff", "cutoff_date_unresolved"]


def test_timeout_retries_and_other_errors_do_not(tmp_path):
    class Timeout(Exception):
        pass

    class UpstreamFailure(Exception):
        pass

    attempts = {"timeout": 0, "error": 0}
    sleeps = []

    def timeout_then_rows(**_kwargs):
        attempts["timeout"] += 1
        if attempts["timeout"] == 1:
            raise Timeout("timed out")
        return [_rows()["played_integer_minutes"]]

    recovered = _adapter(tmp_path / "retry", timeout_then_rows, sleeps=sleeps, retries=2, interval=1)
    result = recovered.fetch_player_logs("9990001", "2024-25", CUTOFF, IDENTITY)
    assert result.state is AnalysisState.AVAILABLE
    assert result.records[0].record.points == 22
    assert attempts["timeout"] == 2
    assert sleeps == [1]

    def always_timeout(**_kwargs):
        attempts["timeout"] += 1
        raise Timeout("timed out")

    exhausted = _adapter(tmp_path / "exhausted", always_timeout, retries=1, interval=0).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        IDENTITY,
    )
    assert exhausted.state is AnalysisState.UNAVAILABLE
    assert exhausted.reason == "provider_timeout"
    assert exhausted.detail == "Timeout"
    assert exhausted.records == ()

    def boom(**_kwargs):
        attempts["error"] += 1
        raise UpstreamFailure("html")

    failed = _adapter(tmp_path / "error", boom, retries=2, interval=1).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        IDENTITY,
    )
    assert failed.reason == "provider_error"
    assert failed.detail == "UpstreamFailure"
    assert attempts["error"] == 1


def test_cache_preserves_the_original_retrieval_time(tmp_path):
    payload = [_rows()["played_integer_minutes"]]
    calls = {"count": 0}
    clocks = iter((RETRIEVED, LATER))

    def source(**_kwargs):
        calls["count"] += 1
        return payload

    adapter = _adapter(tmp_path, source, clock=lambda: next(clocks))
    first = adapter.fetch_player_logs("9990001", "2024-25", CUTOFF, IDENTITY)
    second = adapter.fetch_player_logs("9990001", "2024-25", CUTOFF, IDENTITY)
    assert calls["count"] == 1
    assert first.retrieved_at == RETRIEVED
    assert second.retrieved_at == RETRIEVED
    assert second.records[0].record.available_at == RETRIEVED
    cached = list(tmp_path.rglob("*.json"))
    assert len(cached) == 1
    assert "data" in (ROOT / ".gitignore").read_text(encoding="utf-8")

    cached[0].write_text("{", encoding="utf-8")
    third = adapter.fetch_player_logs("9990001", "2024-25", CUTOFF, IDENTITY)
    assert calls["count"] == 2
    assert third.retrieved_at == LATER


def test_plain_fetch_and_normalization_do_not_use_the_network(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    calls = []
    adapter = _adapter(tmp_path, lambda **kwargs: calls.append(kwargs) or [_rows()["zero_minutes"]])
    refused = adapter.fetch("player_logs")
    assert refused.reason == "player_log_query_required"
    assert calls == []
    result = adapter.fetch_player_logs("9990001", "2024-25", CUTOFF, IDENTITY)
    assert result.records[0].record.minutes == 0.0


def test_nba_api_log_settings_and_enabled_provider_gate():
    status, settings = load_nba_api_log_settings(ROOT / "config" / "nba-api-logs.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert settings is not None
    assert settings.endpoint == "playergamelog"
    assert settings.timeout_seconds == 30
    assert settings.max_retries == 2
    assert settings.min_interval_seconds == 1.0
    assert settings.cache_dir == "data/cache/nba-api"
    assert settings.season_type == "Regular Season"

    rejected, parsed = validate_nba_api_log_settings(
        {
            "version": 1,
            "provider_id": "nba-api",
            "endpoint": "playergamelog",
            "timeout_seconds": 30,
            "max_retries": 2,
            "min_interval_seconds": 0,
            "cache_dir": "data/cache/nba-api",
            "season_type": "Regular Season",
        }
    )
    assert parsed is None
    assert rejected.reason == "invalid_configuration"

    allowed, providers = validate_providers(
        {
            "version": 1,
            "providers": [
                {
                    "provider_id": "nba-api",
                    "enabled": True,
                    "capabilities": ["player_logs"],
                    "supported_markets": [],
                }
            ],
        }
    )
    assert allowed.state is AnalysisState.AVAILABLE
    assert providers is not None
    assert providers.providers[0].enabled is True

    blocked, blocked_providers = validate_providers(
        {
            "version": 1,
            "providers": [
                {
                    "provider_id": "nba-api",
                    "enabled": True,
                    "capabilities": ["player_logs", "markets"],
                    "supported_markets": ["points"],
                }
            ],
        }
    )
    assert blocked_providers is None
    assert blocked.reason == "live_provider_not_implemented"
