"""Live call for stats.nba.com playergamelog.

The endpoint is nba_api.stats.endpoints.PlayerGameLog. The nba_api docs
describe it as one player's box-score lines for one season (parameters
player_id and season; data set player_game_log). PlayerGameLogs is a
separate multi-player endpoint and is not called here.
"""

from typing import cast


def fetch_playergamelog_rows(
    *,
    player_id: str,
    season: str,
    timeout: float,
    season_type: str = "Regular Season",
) -> list[dict[str, object]]:
    from nba_api.stats.endpoints.playergamelog import PlayerGameLog

    endpoint = PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star=season_type,
        timeout=timeout,
    )
    payload = endpoint.player_game_log.get_dict()
    headers = payload["headers"]
    data = payload["data"]
    rows: list[dict[str, object]] = []
    for item in data:
        rows.append({str(header): value for header, value in zip(headers, item)})
    return cast(list[dict[str, object]], rows)


def fetch_commonteamroster_rows(*, team_id: str, season: str, timeout: float) -> list[dict[str, object]]:
    """Call stats.nba.com commonteamroster through nba_api for one team and season."""

    from nba_api.stats.endpoints.commonteamroster import CommonTeamRoster

    endpoint = CommonTeamRoster(team_id=team_id, season=season, timeout=timeout)
    payload = endpoint.common_team_roster.get_dict()
    headers = payload["headers"]
    data = payload["data"]
    rows: list[dict[str, object]] = []
    for item in data:
        rows.append({str(header): value for header, value in zip(headers, item)})
    return cast(list[dict[str, object]], rows)


def fetch_schedule_rows(*, season: str, timeout: float) -> list[dict[str, object]]:
    """Call stats.nba.com scheduleleaguev2 through nba_api.

    One season returns regular-season and playoff games together. The caller
    separates them by the source game id prefix. This is not PlayerGameLogs.
    """

    from nba_api.stats.endpoints.scheduleleaguev2 import ScheduleLeagueV2

    endpoint = ScheduleLeagueV2(season=season, timeout=timeout)
    payload = endpoint.season_games.get_dict()
    headers = payload["headers"]
    data = payload["data"]
    rows: list[dict[str, object]] = []
    for item in data:
        rows.append({str(header): value for header, value in zip(headers, item)})
    return rows
