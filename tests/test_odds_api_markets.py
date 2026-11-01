import copy
from datetime import datetime, timedelta, timezone
import json
import socket

from contracts.common import AnalysisState
from contracts.configuration import (
    PlayerNameMapping,
    load_odds_api_settings,
    load_odds_api_teams,
    load_player_name_map,
    validate_odds_api_settings,
    validate_player_name_map,
    validate_providers,
)
from contracts.schedule import ScheduleGame
from ingestion.adapters.nba_api_schedule import GameDirectory, ResolvedGame
from ingestion.adapters.odds_api_client import event_odds_max_cost, redact_sensitive
from ingestion.adapters.odds_api_markets import OddsApiMarketAdapter, api_key_from_environment, normalize_event_odds
from support import FIXTURES, ROOT, load_json

EVENT = "a512a48a58c4329048174217b2cc7ce0"
RETRIEVED = datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc)
TIP = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
SECRET = "synthetic-secret"
PLAYERS = {"Synthetic Guard A": "player:synthetic-guard-a"}
TEAMS = {"Los Angeles Lakers": "1610612747", "Houston Rockets": "1610612745"}
HEADERS = {"x-requests-remaining": "90", "x-requests-used": "3", "x-requests-last": "3"}


def _directory(tip: datetime = TIP) -> GameDirectory:
    record = ScheduleGame(
        canonical_game_id="game:0022600001",
        snapshot_id="snapshot:nba-sched-0022600001",
        season="2026-27",
        home_team_id="team:1610612747",
        away_team_id="team:1610612745",
        status="scheduled",
        tip_at=tip,
        available_at=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
        source_ids=({"provider_id": "nba-api", "native_id": "0022600001"},),
    )
    resolved = ResolvedGame(
        native_game_id="0022600001",
        canonical_game_id="game:0022600001",
        season_type="Regular Season",
        home_team_id="team:1610612747",
        away_team_id="team:1610612745",
        home_abbreviation="LAL",
        away_abbreviation="HOU",
        tip_at=tip,
        record=record,
    )
    return GameDirectory(
        season="2026-27",
        season_type="Regular Season",
        retrieved_at=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
        abbreviations={"LAL": "team:1610612747", "HOU": "team:1610612745"},
        games={"0022600001": resolved},
        blocked={},
    )


def _mapping(**overrides: object) -> PlayerNameMapping:
    values: dict[str, object] = {
        "provider_name": "Synthetic Guard A",
        "source_name": "Synthetic Guard A",
        "aliases": (),
        "canonical_player_id": "player:synthetic-guard-a",
        "canonical_team_id": "team:1610612747",
        "season": "2026-27",
        "available_at": RETRIEVED - timedelta(hours=1),
        "source_ids": ({"provider_id": "nba-api", "native_id": "commonteamroster:2026-27:1610612747:synthetic"},),
    }
    values.update(overrides)
    return PlayerNameMapping.model_validate(values)


def _payload() -> dict:
    return copy.deepcopy(load_json("odds_api_event_odds.json"))


def _normalize(payload: dict, **overrides: object):
    values = {
        "event_id": EVENT,
        "directory": _directory(),
        "players": PLAYERS,
        "team_names": TEAMS,
        "retrieved_at": RETRIEVED,
        "max_quote_age_seconds": 900,
    }
    values.update(overrides)
    return normalize_event_odds(payload, **values)


def _adapter(tmp_path, transport, **kwargs):
    values = {
        "cache_dir": tmp_path,
        "transport": transport,
        "clock": lambda: RETRIEVED,
        "sleeper": lambda _seconds: None,
        "monotonic": lambda: 0.0,
        "min_interval_seconds": 0,
        "max_retries": 2,
    }
    values.update(kwargs)
    return OddsApiMarketAdapter(**values)


def _ok_transport(payload: dict, calls: list[dict]):
    def transport(**kwargs):
        calls.append(kwargs)
        return {"status": 200, "payload": payload, "headers": dict(HEADERS), "reason": None}

    return transport


def test_api_key_is_read_from_the_odds_api_key_only(monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEY", SECRET)
    assert api_key_from_environment() == ""
    monkeypatch.setenv("THE_ODDS_API_KEY", SECRET)
    assert api_key_from_environment() == SECRET


def test_normalization_keeps_source_update_and_retrieval_time():
    records, quarantined, _match = _normalize(_payload())
    assert quarantined == ()
    assert len(records) == 6
    points = [item.record for item in records if item.record.market.value == "points"]
    assert [item.side.value for item in points] == ["over", "under"]
    over = points[0]
    assert over.canonical_game_id == "game:0022600001"
    assert over.canonical_player_id == "player:synthetic-guard-a"
    assert over.raw_player_name == "Synthetic Guard A"
    assert over.book == "draftkings"
    assert over.line == 24.5
    assert over.american_odds == "-110"
    assert over.captured_at == datetime(2026, 11, 1, 21, 50, tzinfo=timezone.utc)
    assert over.available_at == RETRIEVED
    assert over.captured_at != over.available_at
    assert over.source_ids[0].provider_id == "odds-api"
    assert over.source_ids[0].native_id.startswith(EVENT)
    assert over.source_ids[1].native_id == "0022600001"
    assert over.snapshot_id == f"snapshot:odds-{EVENT}-draftkings-pts-over-24-5"
    assists = next(item.record for item in records if item.record.market.value == "assists" and item.record.side.value == "under")
    assert assists.american_odds == "+100"


def test_ambiguous_player_names_are_not_accepted(tmp_path):
    calls: list[dict] = []
    adapter = _adapter(tmp_path, _ok_transport(_payload(), calls))
    players = {"Synthetic Guard A": "player:synthetic-guard-a", "synthetic guard a": "player:synthetic-guard-b"}
    result = adapter.fetch_event_odds(EVENT, _directory(), players, TEAMS, SECRET)
    assert calls == []
    assert result.records == ()
    assert result.reason == "ambiguous_player_name"


def test_event_mismatch_and_unknown_team_are_quarantined():
    swapped = _payload()
    swapped["home_team"] = "Houston Rockets"
    swapped["away_team"] = "Los Angeles Lakers"
    records, quarantined, _match = _normalize(swapped)
    assert records == ()
    assert _match is None
    assert quarantined[0].reason == "reversed_home_away"

    unknown = _payload()
    unknown["home_team"] = "Los Angeles Laker"
    records, quarantined, _match = _normalize(unknown)
    assert records == ()
    assert quarantined[0].reason == "unknown_team"


def test_tip_window_matches_the_observed_offset_and_rejects_a_wider_gap():
    for seconds in (600, 660):
        schedule_tip = TIP - timedelta(seconds=seconds)
        directory = _directory(tip=schedule_tip)
        records, quarantined, match = _normalize(
            _payload(),
            directory=directory,
            tip_tolerance_seconds=900,
        )
        assert quarantined == ()
        assert len(records) == 6
        assert match is not None
        assert match.method == "home_away_season_within_tip_tolerance"
        assert match.season == "2026-27"
        assert match.schedule_tip_at == schedule_tip
        assert match.odds_commence_at == TIP
        assert match.tip_delta_seconds == seconds
        assert records[0].event_match == match
        assert records[0].record.captured_at != match.schedule_tip_at
        assert records[0].record.available_at != match.odds_commence_at
        assert directory.games["0022600001"].tip_at == schedule_tip
    records, quarantined, _match = _normalize(
        _payload(),
        directory=_directory(tip=TIP - timedelta(seconds=901)),
        tip_tolerance_seconds=900,
    )
    assert records == ()
    assert quarantined[0].reason == "event_mismatch"
    records, quarantined, _match = _normalize(
        _payload(),
        directory=_directory(tip=TIP - timedelta(seconds=600)),
    )
    assert records == ()
    assert quarantined[0].reason == "event_mismatch"


def test_two_games_inside_the_tip_window_are_ambiguous():
    base = _directory()
    later_tip = TIP + timedelta(seconds=600)
    later_record = base.games["0022600001"].record.model_copy(
        update={"canonical_game_id": "game:0022600002", "tip_at": later_tip}
    )
    later = base.games["0022600001"].model_copy(
        update={
            "native_game_id": "0022600002",
            "canonical_game_id": "game:0022600002",
            "tip_at": later_tip,
            "record": later_record,
        }
    )
    directory = base.model_copy(update={"games": {"0022600001": base.games["0022600001"], "0022600002": later}})
    records, quarantined, _match = _normalize(_payload(), directory=directory, tip_tolerance_seconds=900)
    assert records == ()
    assert quarantined[0].reason == "ambiguous_event"


def test_a_passed_schedule_tip_is_post_tip_before_the_odds_commence_time():
    records, quarantined, post_match = _normalize(
        _payload(),
        directory=_directory(tip=TIP - timedelta(seconds=600)),
        retrieved_at=TIP - timedelta(seconds=300),
        tip_tolerance_seconds=900,
    )
    assert records == ()
    assert quarantined[0].reason == "post_tip"
    assert post_match is not None
    assert post_match.schedule_tip_at == TIP - timedelta(seconds=600)
    assert post_match.odds_commence_at == TIP
    assert post_match.tip_delta_seconds == 600


def test_a_different_season_in_the_tip_window_is_not_the_same_game():
    base = _directory()
    other_record = base.games["0022600001"].record.model_copy(update={"season": "2025-26"})
    other = base.games["0022600001"].model_copy(update={"record": other_record})
    directory = base.model_copy(update={"games": {"0022600001": other}})
    records, quarantined, match = _normalize(_payload(), directory=directory, tip_tolerance_seconds=900)
    assert records == ()
    assert match is None
    assert quarantined[0].reason == "season_mismatch"


def test_missing_markets_are_not_fabricated():
    payload = _payload()
    payload["bookmakers"][0]["markets"] = [payload["bookmakers"][0]["markets"][0]]
    records, quarantined, _match = _normalize(payload)
    assert {item.record.market.value for item in records} == {"points"}
    assert [item.reason for item in quarantined] == ["missing_market", "missing_market"]
    assert [item.detail for item in quarantined] == ["player_assists", "player_rebounds"]


def test_mismatched_lines_are_not_paired():
    payload = _payload()
    market = payload["bookmakers"][0]["markets"][0]
    market["outcomes"][1]["point"] = 25.5
    records, quarantined, _match = _normalize(payload)
    assert [item.record.market.value for item in records] == ["assists", "assists", "rebounds", "rebounds"]
    assert any(item.reason == "mismatched_line" and item.detail == "synthetic guard a" for item in quarantined)


def test_invalid_odds_are_quarantined():
    payload = _payload()
    market = payload["bookmakers"][0]["markets"][0]
    market["outcomes"][0]["price"] = 50
    records, quarantined, _match = _normalize(payload)
    reasons = [item.reason for item in quarantined]
    assert "invalid_odds" in reasons
    assert "unpaired_side" in reasons
    assert "points" not in {item.record.market.value for item in records}


def test_stale_future_and_post_tip_quotes_are_rejected():
    stale = _payload()
    stale["bookmakers"][0]["markets"][0]["last_update"] = "2026-11-01T21:00:00Z"
    records, quarantined, _match = _normalize(stale)
    assert any(item.reason == "stale" for item in quarantined)
    assert "points" not in {item.record.market.value for item in records}

    future = _payload()
    future["bookmakers"][0]["markets"][0]["last_update"] = "2026-11-01T22:05:00Z"
    _records, quarantined, _match = _normalize(future)
    assert any(item.reason == "future_dated" for item in quarantined)

    split = _payload()
    points = split["bookmakers"][0]["markets"][0]
    under = points["outcomes"].pop()
    points["last_update"] = "2026-11-01T21:50:00Z"
    split["bookmakers"][0]["markets"].insert(0, {"key": "player_points", "last_update": "2026-11-01T21:40:00Z", "outcomes": [under]})
    _records, quarantined, _match = _normalize(split)
    assert any(item.reason == "incompatible_timestamps" for item in quarantined)

    post_tip, quarantined, post_match = _normalize(_payload(), retrieved_at=TIP)
    assert post_tip == ()
    assert quarantined[0].reason == "post_tip"


def test_cache_prevents_a_second_request(tmp_path):
    calls: list[dict] = []
    later = {"at": RETRIEVED}

    def clock():
        return later["at"]

    adapter = _adapter(tmp_path, _ok_transport(_payload(), calls), clock=clock)
    first = adapter.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    later["at"] = RETRIEVED + timedelta(minutes=5)
    second = adapter.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    assert len(calls) == 1
    assert first.retrieved_at == RETRIEVED
    assert second.retrieved_at == RETRIEVED
    assert second.quota is not None
    assert second.quota.last_cost == 3
    cached = list(tmp_path.rglob("*.json"))
    assert cached
    assert SECRET not in cached[0].read_text(encoding="utf-8")


def test_quota_and_authentication_failures_are_not_retried(tmp_path):
    calls = {"n": 0}

    def transport(**_kwargs):
        calls["n"] += 1
        status = 401 if calls["n"] == 1 else 429
        reason = "authentication_failed" if status == 401 else "rate_limited"
        return {"status": status, "payload": None, "headers": {"x-requests-remaining": "0"}, "reason": reason}

    adapter = _adapter(tmp_path, transport)
    auth = adapter.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    assert auth.reason == "authentication_failed"
    assert auth.quota is not None
    assert auth.quota.remaining == 0
    limited = adapter.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    assert limited.reason == "rate_limited"
    assert calls["n"] == 2


def test_secrets_are_redacted_from_errors_and_cache(tmp_path):
    leaked = f"https://api.the-odds-api.com/v4/sports/basketball_nba/events?apiKey={SECRET}&regions=us"
    assert SECRET not in redact_sensitive(leaked, SECRET)
    assert "apiKey=REDACTED" in redact_sensitive(leaked, SECRET)

    def transport(**_kwargs):
        raise RuntimeError(leaked)

    adapter = _adapter(tmp_path, transport, max_retries=0)
    result = adapter.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    encoded = result.model_dump_json()
    assert SECRET not in encoded
    assert "apiKey=" not in encoded
    assert result.detail == "RuntimeError"
    payload = _payload()
    payload["bookmakers"][0]["markets"][0]["outcomes"][0]["description"] = SECRET
    calls: list[dict] = []
    poisoned = _adapter(tmp_path / "poison", _ok_transport(payload, calls))
    hidden = poisoned.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    assert hidden.reason == "secret_in_payload"
    assert SECRET not in hidden.model_dump_json()
    assert list((tmp_path / "poison").rglob("*.json")) == []


def test_credit_cap_blocks_the_request_before_transport(tmp_path):
    calls: list[dict] = []
    books = tuple(f"book{index}" for index in range(11))
    adapter = _adapter(tmp_path, _ok_transport(_payload(), calls), bookmakers=books)
    assert event_odds_max_cost(markets=("player_points", "player_assists", "player_rebounds"), regions=("us",), bookmakers=books) == 6
    result = adapter.fetch_event_odds(EVENT, _directory(), PLAYERS, TEAMS, SECRET)
    assert calls == []
    assert result.reason == "credit_cap_exceeded"
    assert result.documented_max_cost == 6


def test_plain_fetch_does_not_use_the_network(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", boom)
    calls: list[dict] = []
    adapter = _adapter(tmp_path, _ok_transport(_payload(), calls))
    refused = adapter.fetch("markets", market="points")
    assert refused.reason == "markets_query_required"
    assert calls == []
    records, _quarantined, _match = _normalize(_payload())
    assert len(records) == 6


def test_odds_settings_and_enabled_provider_gate():
    status, settings = load_odds_api_settings(ROOT / "config" / "odds-api.example.yaml")
    assert status.state is AnalysisState.AVAILABLE
    assert settings is not None
    assert settings.api_key_env == "THE_ODDS_API_KEY"
    assert settings.freshness_limit_validated is False
    assert settings.max_quote_age_seconds == 900
    assert settings.max_request_credits == 3
    assert settings.tip_tolerance_seconds == 900
    assert settings.markets == ("player_points", "player_assists", "player_rebounds")
    assert settings.player_map == "config/odds-player-map.json"
    teams_status, teams = load_odds_api_teams(ROOT / settings.team_names)
    assert teams_status.state is AnalysisState.AVAILABLE
    assert teams is not None
    assert len(teams.teams) == 30
    assert next(item.nba_team_id for item in teams.teams if item.name == "Los Angeles Lakers") == "1610612747"

    rejected, parsed = validate_odds_api_settings(
        {
            "version": 1,
            "provider_id": "odds-api",
            "endpoint": "event-odds",
            "sport": "basketball_nba",
            "region": "us",
            "markets": ["player_points", "player_assists", "player_rebounds"],
            "timeout_seconds": 30,
            "max_retries": 2,
            "min_interval_seconds": 1,
            "cache_dir": "data/cache/odds-api",
            "api_key_env": "THE_ODDS_API_KEY",
            "team_names": "config/odds-api-teams.json",
            "max_quote_age_seconds": 900,
            "freshness_limit_validated": True,
            "max_request_credits": 3,
        }
    )
    assert parsed is None
    assert rejected.reason == "invalid_configuration"

    allowed, providers = validate_providers(
        {
            "version": 1,
            "providers": [
                {
                    "provider_id": "odds-api",
                    "enabled": True,
                    "capabilities": ["markets"],
                    "supported_markets": ["points", "assists", "rebounds"],
                }
            ],
        }
    )
    assert allowed.state is AnalysisState.AVAILABLE
    assert providers is not None

    fixture = json.loads((FIXTURES / "odds_api_event_odds.json").read_text(encoding="utf-8"))
    assert fixture["id"] == EVENT


def test_exact_identity_keeps_odds_clocks_and_original_name():
    identity = _mapping()
    records, quarantined, _match = _normalize(_payload(), identities=(identity,))
    assert quarantined == ()
    assert len(records) == 6
    over = next(item for item in records if item.record.market.value == "points" and item.record.side.value == "over")
    assert over.record.canonical_player_id == "player:synthetic-guard-a"
    assert over.record.raw_player_name == "Synthetic Guard A"
    assert over.record.captured_at == datetime(2026, 11, 1, 21, 50, tzinfo=timezone.utc)
    assert over.record.available_at == RETRIEVED
    assert over.identity_available_at == identity.available_at
    assert over.identity_available_at < over.record.available_at


def test_explicit_alias_resolves_and_near_spelling_does_not():
    aliased = _mapping(
        provider_name="Synthetic Guard A.",
        source_name="Synthetic Guard A.",
        aliases=("Synthetic Guard A",),
    )
    records, quarantined, _match = _normalize(_payload(), identities=(aliased,))
    assert quarantined == ()
    assert records[0].record.raw_player_name == "Synthetic Guard A"
    assert records[0].record.canonical_player_id == "player:synthetic-guard-a"

    near = _mapping(provider_name="Synthetic Guard A.", source_name="Synthetic Guard A.")
    missed, missed_quarantine, _match = _normalize(_payload(), identities=(near,))
    assert missed == ()
    assert {item.reason for item in missed_quarantine} == {"unresolved_player_name"}
    assert {item.detail for item in missed_quarantine} == {"Synthetic Guard A"}

    spaced = _mapping(provider_name="  synthetic   guard a", source_name="  synthetic   guard a")
    spaced_records, spaced_quarantine, _match = _normalize(_payload(), identities=(spaced,))
    assert spaced_quarantine == ()
    assert spaced_records[0].record.raw_player_name == "Synthetic Guard A"


def test_duplicate_names_wrong_team_and_missing_mapping_stay_quarantined():
    first = _mapping()
    second = _mapping(
        provider_name="Other Player",
        source_name="Other Player",
        aliases=("Synthetic Guard A",),
        canonical_player_id="player:synthetic-other",
        canonical_team_id="team:1610612745",
    )
    records, quarantined, match = _normalize(_payload(), identities=(first, second))
    assert records == ()
    assert match is None
    assert quarantined[0].reason == "ambiguous_player_name"

    wrong = _mapping(canonical_team_id="team:1610612738")
    records, quarantined, match = _normalize(_payload(), identities=(wrong,))
    assert records == ()
    assert match is not None
    assert {item.reason for item in quarantined} == {"wrong_team"}
    assert quarantined[0].identity_available_at == wrong.available_at
    assert quarantined[0].detail == "Synthetic Guard A"

    missing = _mapping(
        provider_name="Someone Else",
        source_name="Someone Else",
        canonical_player_id="player:someone-else",
    )
    records, quarantined, _match = _normalize(_payload(), identities=(missing,))
    assert records == ()
    assert {item.reason for item in quarantined} == {"unresolved_player_name"}


def test_identity_after_retrieval_does_not_rewrite_quotes_or_bypass_pregame():
    late = _mapping(available_at=RETRIEVED + timedelta(minutes=30))
    records, quarantined, match = _normalize(_payload(), identities=(late,))
    assert records == ()
    assert match is not None
    assert match.schedule_tip_at == TIP
    assert match.odds_commence_at == TIP
    assert {item.reason for item in quarantined} == {"identity_not_yet_available"}
    assert {item.identity_available_at for item in quarantined} == {late.available_at}
    assert {item.market for item in quarantined} == {"player_points", "player_assists", "player_rebounds"}

    early = _mapping(available_at=TIP - timedelta(hours=1))
    records, quarantined, match = _normalize(_payload(), retrieved_at=TIP, identities=(early,))
    assert records == ()
    assert match is not None
    assert quarantined[0].reason == "post_tip"
    assert match.schedule_tip_at == TIP
    assert match.odds_commence_at == TIP


def test_missing_side_stays_unpaired_when_identity_is_known():
    payload = _payload()
    outcomes = payload["bookmakers"][0]["markets"][0]["outcomes"]
    payload["bookmakers"][0]["markets"][0]["outcomes"] = [outcomes[0]]
    records, quarantined, _match = _normalize(payload, identities=(_mapping(),))
    assert len(records) == 4
    assert {item.record.market.value for item in records} == {"assists", "rebounds"}
    unpaired = [item for item in quarantined if item.reason == "unpaired_side"]
    assert len(unpaired) == 1
    assert unpaired[0].market == "player_points"
    assert all(item.record.side.value in {"over", "under"} for item in records)


def test_published_player_map_keeps_reviewed_aliases_and_evidence_time():
    status, player_map = load_player_name_map(ROOT / "config" / "odds-player-map.json")
    assert status.state is AnalysisState.AVAILABLE
    assert player_map is not None
    assert player_map.season == "2026-27"
    assert player_map.retrieved_at == datetime(2026, 11, 1, 0, 39, 38, 453334, tzinfo=timezone.utc)
    assert len(player_map.players) == 14
    by_name = {item.provider_name: item for item in player_map.players}
    assert by_name["Jaylin Williams"].canonical_player_id == "player:1631119"
    assert by_name["Jaylin Williams"].canonical_team_id == "team:1610612760"
    assert "Jalen Williams" not in by_name
    assert by_name["Jabari Smith Jr"].aliases == ("Jabari Smith Jr.",)
    assert by_name["Jabari Smith Jr"].source_name == "Jabari Smith Jr."
    assert by_name["Bogdan Bogdanovic"].source_name == "Bogdan Bogdanović"
    assert by_name["Bogdan Bogdanovic"].aliases == ("Bogdan Bogdanović",)
    assert by_name["Shai Gilgeous-Alexander"].canonical_player_id == "player:1628983"
    teams = {item.canonical_team_id for item in player_map.players}
    assert teams == {"team:1610612745", "team:1610612760"}

    payload = json.loads((ROOT / "config" / "odds-player-map.json").read_text(encoding="utf-8"))
    extra = dict(payload["players"][0])
    extra["canonical_player_id"] = "player:1643531"
    extra["provider_name"] = "Aday Mara"
    payload["players"].append(extra)
    rejected, parsed = validate_player_name_map(payload)
    assert parsed is None
    assert rejected.reason == "invalid_configuration"

    payload = json.loads((ROOT / "config" / "odds-player-map.json").read_text(encoding="utf-8"))
    payload["players"][0]["source_name"] = "Aday Mara."
    payload["players"][0]["aliases"] = []
    rejected, parsed = validate_player_name_map(payload)
    assert parsed is None
    assert rejected.reason == "invalid_configuration"
