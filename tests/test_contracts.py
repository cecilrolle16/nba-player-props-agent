from datetime import datetime

import pytest
from pydantic import ValidationError

from contracts.common import AnalysisState
from contracts.injury import InjuryReport
from contracts.market import MarketSnapshot
from contracts.player_log import PlayerGameLog
from contracts.projection import Projection
from contracts.roster import RosterEntry
from contracts.schedule import ScheduleGame
from contracts.team_context import TeamContext, metric_state
from support import load_subject

SOURCE = [{"provider_id": "synthetic-records", "native_id": "row-1"}]
WHEN = "2026-11-01T22:00:00Z"


def test_canonical_records_round_trip():
    schedule = ScheduleGame.model_validate(
        {
            "canonical_game_id": "game:synthetic-2026-11-01-home-away",
            "season": "2025-26",
            "home_team_id": "team:synthetic-home",
            "away_team_id": "team:synthetic-away",
            "status": "scheduled",
            "tip_at": "2026-11-02T00:00:00Z",
            "available_at": WHEN,
            "source_ids": SOURCE,
        }
    )
    roster = RosterEntry.model_validate(
        {
            "canonical_player_id": "player:synthetic-guard-a",
            "canonical_team_id": "team:synthetic-home",
            "season": "2025-26",
            "display_name": "Synthetic Guard A",
            "active": True,
            "available_at": WHEN,
            "source_ids": SOURCE,
        }
    )
    log = PlayerGameLog.model_validate(
        {
            "canonical_player_id": "player:synthetic-guard-a",
            "canonical_game_id": "game:synthetic-2026-11-01-home-away",
            "canonical_team_id": "team:synthetic-home",
            "points": 27,
            "assists": 6,
            "rebounds": 4,
            "minutes": 34.5,
            "available_at": "2026-11-02T02:30:00Z",
            "source_ids": SOURCE,
        }
    )
    context = TeamContext.model_validate(
        {
            "canonical_team_id": "team:synthetic-home",
            "canonical_game_id": "game:synthetic-2026-11-01-home-away",
            "opponent_team_id": "team:synthetic-away",
            "is_home": True,
            "pace": None,
            "defensive_rating": 112.0,
            "snapshot_id": "snapshot:synthetic-points-1",
            "available_at": WHEN,
            "source_ids": SOURCE,
        }
    )
    injury = InjuryReport.model_validate(load_subject("valid_points.json").injury.model_dump(mode="json"))
    projection = Projection.model_validate(
        load_subject("valid_points.json").historical_projections[0].model_dump(mode="json")
    )
    snapshot = MarketSnapshot.model_validate(load_subject("valid_points.json").snapshot.model_dump(mode="json"))
    for record in (schedule, roster, log, context, injury, projection, snapshot):
        restored = type(record).model_validate(record.model_dump(mode="json"))
        assert restored == record
    assert metric_state(context.pace) is AnalysisState.UNAVAILABLE
    assert metric_state(context.defensive_rating) is AnalysisState.AVAILABLE


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        RosterEntry.model_validate(
            {
                "canonical_player_id": "player:synthetic-guard-a",
                "canonical_team_id": "team:synthetic-home",
                "season": "2025-26",
                "display_name": "Synthetic Guard A",
                "active": True,
                "available_at": datetime(2026, 11, 1, 22, 0, tzinfo=None),
                "source_ids": SOURCE,
            }
        )


def test_non_utc_offset_is_rejected():
    with pytest.raises(ValidationError):
        RosterEntry.model_validate(
            {
                "canonical_player_id": "player:synthetic-guard-a",
                "canonical_team_id": "team:synthetic-home",
                "season": "2025-26",
                "display_name": "Synthetic Guard A",
                "active": True,
                "available_at": "2026-11-01T18:00:00-04:00",
                "source_ids": SOURCE,
            }
        )


def test_canonical_snapshot_rejects_malformed_odds():
    payload = load_subject("malformed_odds.json").snapshot.model_dump(mode="json")
    payload["canonical_player_id"] = "player:synthetic-guard-a"
    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate(payload)
