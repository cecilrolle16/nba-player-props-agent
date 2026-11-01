import socket
from datetime import datetime, timezone

from contracts.common import AnalysisState
from contracts.configuration import load_nba_api_roster_settings, load_team_abbreviations, validate_providers
from ingestion.adapters.nba_api_roster import NbaApiRosterAdapter, build_roster
from ingestion.adapters.nba_api_schedule import TeamAbbreviationMap
from support import ROOT


RETRIEVED = datetime(2026, 11, 1, 0, 39, 38, tzinfo=timezone.utc)
HOU = "1610612745"
OKC = "1610612760"


def _map() -> TeamAbbreviationMap:
    status, parsed = load_team_abbreviations(ROOT / "tests" / "fixtures" / "nba_team_abbreviations.json")
    assert status.state is AnalysisState.AVAILABLE
    assert parsed is not None
    return TeamAbbreviationMap.from_file(parsed)


def _row(
    player_id: int = 1631095,
    name: str = "Jabari Smith Jr.",
    team_id: str = HOU,
    season: str = "2026",
    status: object = 0,
) -> dict[str, object]:
    return {
        "TeamID": int(team_id),
        "SEASON": season,
        "LeagueID": "00",
        "PLAYER": name,
        "PLAYER_ID": player_id,
        "SUPPLEMENTAL_STATUS": status,
    }


def test_roster_ids_come_from_the_source_row_and_the_name_is_preserved():
    records, quarantined = build_roster(
        [_row(name="Bogdan Bogdanović")],
        season="2026-27",
        team_id=HOU,
        retrieved_at=RETRIEVED,
    )
    assert quarantined == ()
    assert len(records) == 1
    record = records[0]
    assert record.canonical_player_id == "player:1631095"
    assert record.canonical_team_id == "team:1610612745"
    assert record.display_name == "Bogdan Bogdanović"
    assert record.season == "2026-27"
    assert record.active is True
    assert record.available_at == RETRIEVED
    assert record.source_ids[0].native_id == "commonteamroster:2026-27:1610612745:1631095"


def test_wrong_team_season_duplicate_and_unknown_status_are_quarantined():
    rows = [
        _row(player_id=201142, name="Kevin Durant", team_id=OKC),
        _row(player_id=203935, name="Marcus Smart", season="2025"),
        _row(player_id=1631106, name="Tari Eason"),
        _row(player_id=1631106, name="Tari Eason"),
        _row(player_id=1641708, name="Amen Thompson", status=9),
        _row(player_id=0, name="Missing Id"),
    ]
    records, quarantined = build_roster(rows, season="2026-27", team_id=HOU, retrieved_at=RETRIEVED)
    assert records == ()
    reasons = {item.reason for item in quarantined}
    assert reasons == {
        "team_mismatch",
        "season_mismatch",
        "duplicate_player",
        "unknown_roster_status",
        "invalid_player_id",
    }
    assert sum(item.reason == "duplicate_player" for item in quarantined) == 2


def test_plain_fetch_does_not_use_the_network(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    calls: list[str] = []

    def source(**_kwargs):
        calls.append("called")
        return []

    adapter = NbaApiRosterAdapter(_map(), cache_dir=tmp_path, row_source=source, max_retries=0)
    refused = adapter.fetch("rosters")
    assert refused.reason == "roster_query_required"
    unknown = adapter.fetch_roster("1610612799", "2026-27")
    assert unknown.reason == "unknown_team"
    assert calls == []


def test_cache_keeps_the_original_retrieval_time(tmp_path):
    calls: list[str] = []
    clock = {"at": RETRIEVED}

    def source(**_kwargs):
        calls.append("called")
        return [_row()]

    adapter = NbaApiRosterAdapter(
        _map(),
        cache_dir=tmp_path,
        row_source=source,
        clock=lambda: clock["at"],
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
        min_interval_seconds=0,
        max_retries=0,
    )
    first = adapter.fetch_roster(HOU, "2026-27")
    clock["at"] = RETRIEVED.replace(hour=2)
    second = adapter.fetch_roster(HOU, "2026-27")
    assert calls == ["called"]
    assert first.retrieved_at == RETRIEVED
    assert second.retrieved_at == RETRIEVED
    assert second.records[0].available_at == RETRIEVED
    assert second.records[0].display_name == "Jabari Smith Jr."


def test_roster_settings_and_enabled_provider_gate():
    status, settings = load_nba_api_roster_settings(ROOT / "config" / "nba-api-roster.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert settings is not None
    assert settings.endpoint == "commonteamroster"
    assert settings.team_map == "config/nba-team-abbreviations.json"
    allowed, providers = validate_providers(
        {
            "version": 1,
            "providers": [
                {
                    "provider_id": "nba-api",
                    "enabled": True,
                    "capabilities": ["player_logs", "schedules", "rosters"],
                    "supported_markets": [],
                }
            ],
        }
    )
    assert allowed.state is AnalysisState.AVAILABLE
    assert providers is not None
