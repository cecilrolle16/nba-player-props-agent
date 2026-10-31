import socket
from datetime import datetime, timezone

from contracts.common import AnalysisState
from contracts.configuration import (
    load_nba_api_schedule_settings,
    load_team_abbreviations,
    validate_providers,
)
from ingestion.adapters.nba_api_logs import NbaApiPlayerLogAdapter
from ingestion.adapters.nba_api_schedule import NbaApiScheduleAdapter, TeamAbbreviationMap, build_game_directory
from support import ROOT


RETRIEVED = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2025, 2, 1, 18, 0, tzinfo=timezone.utc)
LAL = "1610612747"
HOU = "1610612745"
BOS = "1610612738"


def _map() -> TeamAbbreviationMap:
    status, parsed = load_team_abbreviations(ROOT / "tests" / "fixtures" / "nba_team_abbreviations.json")
    assert status.state is AnalysisState.AVAILABLE
    assert parsed is not None
    return TeamAbbreviationMap.from_file(parsed)


def _game(
    game_id: str,
    home: tuple[str, str],
    away: tuple[str, str],
    tip: str,
    *,
    status: int = 3,
    neutral: bool = False,
    season_year: str = "2024",
) -> dict[str, object]:
    return {
        "seasonYear": season_year,
        "gameId": game_id,
        "gameDateTimeUTC": tip,
        "gameStatus": status,
        "gameStatusText": "Final" if status == 3 else "Scheduled",
        "postponedStatus": "",
        "isNeutral": neutral,
        "homeTeam_teamId": home[1],
        "homeTeam_teamTricode": home[0],
        "awayTeam_teamId": away[1],
        "awayTeam_teamTricode": away[0],
    }


def _log(game_id: str, matchup: str, date: str, points: int) -> dict[str, object]:
    return {
        "Player_ID": 9990001,
        "Game_ID": game_id,
        "GAME_DATE": date,
        "MATCHUP": matchup,
        "MIN": 30,
        "PTS": points,
        "AST": 5,
        "REB": 4,
    }


def _directory(rows: list[dict[str, object]], season_type: str = "Regular Season"):
    return build_game_directory(
        rows,
        season="2024-25",
        season_type=season_type,
        team_map=_map(),
        retrieved_at=RETRIEVED,
    )


def test_multiple_games_use_source_ids_without_a_manual_game_map(tmp_path):
    rows = [
        _game("0022400001", ("LAL", LAL), ("HOU", HOU), "2025-01-10T03:00:00Z"),
        _game("0022400002", ("HOU", HOU), ("LAL", LAL), "2025-01-14T03:00:00Z"),
    ]
    directory, quarantined = _directory(rows)
    assert quarantined == ()
    assert set(directory.games) == {"0022400001", "0022400002"}
    first = directory.games["0022400001"]
    assert first.canonical_game_id == "game:0022400001"
    assert first.home_team_id == f"team:{LAL}"
    assert first.away_team_id == f"team:{HOU}"
    assert first.record.available_at == RETRIEVED
    assert first.record.tip_at == datetime(2025, 1, 10, 3, 0, tzinfo=timezone.utc)
    assert first.record.source_ids[0].native_id == "scheduleleaguev2:0022400001"
    assert first.record.available_at != first.record.tip_at

    logs = [
        _log("0022400001", "LAL vs. HOU", "JAN 10, 2025", 22),
        _log("0022400002", "LAL @ HOU", "JAN 14, 2025", 18),
    ]
    result = NbaApiPlayerLogAdapter(
        cache_dir=tmp_path,
        row_source=lambda **_kwargs: logs,
        clock=lambda: RETRIEVED,
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
        min_interval_seconds=0,
    ).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        {"players": {"9990001": "player:synthetic-guard-a"}, "teams": {}, "games": {}},
        game_directory=directory,
    )
    assert result.state is AnalysisState.AVAILABLE
    assert result.quarantined == ()
    assert [item.record.canonical_game_id for item in result.records] == [
        "game:0022400001",
        "game:0022400002",
    ]
    assert result.records[0].record.canonical_team_id == f"team:{LAL}"
    assert result.records[1].record.canonical_team_id == f"team:{LAL}"
    assert result.records[0].record.points == 22
    assert result.records[1].record.points == 18


def test_duplicate_mappings_keep_one_game(tmp_path):
    row = _game("0022400001", ("LAL", LAL), ("HOU", HOU), "2025-01-10T03:00:00Z")
    calls = []

    def source(**kwargs):
        calls.append(kwargs["season"])
        return [row, dict(row)]

    fetch = NbaApiScheduleAdapter(
        _map(),
        cache_dir=tmp_path,
        row_source=source,
        clock=lambda: RETRIEVED,
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
        min_interval_seconds=0,
    ).fetch_schedule("2024-25", "Regular Season")
    assert calls == ["2024-25"]
    assert len(fetch.records) == 1
    assert [item.reason for item in fetch.quarantined] == ["duplicate_mapping"]
    assert fetch.directory is not None
    assert fetch.directory.games["0022400001"].canonical_game_id == "game:0022400001"


def test_conflicting_game_rows_are_not_accepted():
    original = _game("0022400002", ("HOU", HOU), ("LAL", LAL), "2025-01-14T03:00:00Z")
    conflict = _game("0022400002", ("BOS", BOS), ("LAL", LAL), "2025-01-14T03:00:00Z")
    directory, quarantined = _directory([original, conflict])
    assert directory.games == {}
    assert directory.blocked["0022400002"] == "conflicting_game_identity"
    assert {item.reason for item in quarantined} == {"conflicting_game_identity"}


def test_unknown_abbreviation_and_matchup_conflict_are_quarantined(tmp_path):
    rows = [
        _game("0022400001", ("LAL", LAL), ("HOU", HOU), "2025-01-10T03:00:00Z"),
        _game("0022400009", ("ZZZ", "1610612999"), ("HOU", HOU), "2025-01-11T03:00:00Z"),
        _game("0022400008", ("LAL", HOU), ("HOU", LAL), "2025-01-12T03:00:00Z"),
    ]
    directory, quarantined = _directory(rows)
    assert "0022400001" in directory.games
    assert "0022400009" not in directory.games
    assert "0022400008" not in directory.games
    reasons = {item.native_game_id: item.reason for item in quarantined}
    assert reasons["0022400009"] == "unknown_abbreviation"
    assert reasons["0022400008"] == "team_conflict"

    logs = [
        _log("0022400001", "ZZZ vs. HOU", "JAN 10, 2025", 10),
        _log("0022400001", "LAL @ HOU", "JAN 10, 2025", 11),
    ]
    result = NbaApiPlayerLogAdapter(
        cache_dir=tmp_path,
        row_source=lambda **_kwargs: logs,
        clock=lambda: RETRIEVED,
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
        min_interval_seconds=0,
    ).fetch_player_logs(
        "9990001",
        "2024-25",
        CUTOFF,
        {"players": {"9990001": "player:synthetic-guard-a"}, "teams": {}, "games": {}},
        game_directory=directory,
    )
    assert result.records == ()
    assert [item.reason for item in result.quarantined] == ["unknown_abbreviation", "team_conflict"]


def test_regular_season_and_playoffs_stay_separate():
    rows = [
        _game("0022400001", ("LAL", LAL), ("HOU", HOU), "2025-01-10T03:00:00Z"),
        _game("0042400001", ("BOS", BOS), ("LAL", LAL), "2025-04-20T00:00:00Z"),
    ]
    regular, _regular_quarantine = _directory(rows, "Regular Season")
    playoffs, _playoff_quarantine = _directory(rows, "Playoffs")
    assert set(regular.games) == {"0022400001"}
    assert regular.blocked["0042400001"] == "season_type_mismatch"
    assert set(playoffs.games) == {"0042400001"}
    assert playoffs.blocked["0022400001"] == "season_type_mismatch"
    assert playoffs.games["0042400001"].record.season == "2024-25"
    assert regular.season_type == "Regular Season"
    assert playoffs.season_type == "Playoffs"


def test_strict_point_in_time_rejects_a_schedule_retrieved_after_the_cutoff(tmp_path):
    log_retrieved = datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)
    schedule_retrieved = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    directory, quarantined = build_game_directory(
        [_game("0022400001", ("LAL", LAL), ("HOU", HOU), "2025-01-10T03:00:00Z")],
        season="2024-25",
        season_type="Regular Season",
        team_map=_map(),
        retrieved_at=schedule_retrieved,
    )
    assert quarantined == ()
    assert directory.games["0022400001"].record.snapshot_id == "snapshot:nba-sched-0022400001"
    assert directory.games["0022400001"].record.available_at == schedule_retrieved
    adapter = NbaApiPlayerLogAdapter(
        cache_dir=tmp_path,
        row_source=lambda **_kwargs: [_log("0022400001", "LAL vs. HOU", "JAN 10, 2025", 22)],
        clock=lambda: log_retrieved,
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
        min_interval_seconds=0,
    )
    identity = {"players": {"9990001": "player:synthetic-guard-a"}, "teams": {}, "games": {}}
    blocked = adapter.fetch_player_logs(
        "9990001",
        "2024-25",
        cutoff,
        identity,
        game_directory=directory,
        strict_point_in_time=True,
    )
    assert blocked.records == ()
    assert [item.reason for item in blocked.quarantined] == ["schedule_not_yet_available"]
    assert blocked.retrieved_at == log_retrieved

    joined = adapter.fetch_player_logs(
        "9990001",
        "2024-25",
        datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
        identity,
        game_directory=directory,
        strict_point_in_time=True,
    )
    assert len(joined.records) == 1
    item = joined.records[0]
    assert item.log_retrieved_at == log_retrieved
    assert item.record.available_at == schedule_retrieved
    assert item.snapshot_id == "snapshot:nba-api-0022400001"
    assert item.schedule_snapshot_id == "snapshot:nba-sched-0022400001"
    assert item.schedule_snapshot_id != item.snapshot_id


def test_schedule_cache_and_settings_do_not_use_the_network(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    row = _game("0022400001", ("LAL", LAL), ("HOU", HOU), "2025-01-10T03:00:00Z")
    clocks = iter((RETRIEVED, datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)))
    calls = {"count": 0}

    def source(**_kwargs):
        calls["count"] += 1
        return [row]

    adapter = NbaApiScheduleAdapter(
        _map(),
        cache_dir=tmp_path,
        row_source=source,
        clock=lambda: next(clocks),
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
        min_interval_seconds=0,
    )
    refused = adapter.fetch("schedules")
    assert refused.reason == "schedule_query_required"
    assert calls["count"] == 0
    first = adapter.fetch_schedule("2024-25", "Regular Season")
    second = adapter.fetch_schedule("2024-25", "Playoffs")
    assert calls["count"] == 1
    assert first.retrieved_at == RETRIEVED
    assert second.retrieved_at == RETRIEVED
    assert second.records == ()
    assert second.directory is not None
    assert second.directory.blocked["0022400001"] == "season_type_mismatch"

    status, settings = load_nba_api_schedule_settings(ROOT / "config" / "nba-api-schedule.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert settings is not None
    assert settings.endpoint == "scheduleleaguev2"
    shipped_status, shipped = load_team_abbreviations(ROOT / settings.team_map)
    assert shipped_status.state is AnalysisState.AVAILABLE
    assert shipped is not None
    assert len(shipped.teams) == 30
    assert {item.abbreviation: item.nba_team_id for item in shipped.teams}["LAL"] == LAL

    allowed, providers = validate_providers(
        {
            "version": 1,
            "providers": [
                {
                    "provider_id": "nba-api",
                    "enabled": True,
                    "capabilities": ["player_logs", "schedules"],
                    "supported_markets": [],
                }
            ],
        }
    )
    assert allowed.state is AnalysisState.AVAILABLE
    assert providers is not None
