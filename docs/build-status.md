# Build status

Step: provenance for joined player-log and schedule evidence. Stopped here.

Custom rules were not changed. No projection math, odds client, numeric adjustments, or AWS resources were added.

## Test results

`python -m pytest` — 77 passed in 0.89s (Python 3.12.10). No warnings.

Automated tests use synthetic fixtures and do not call the network. No API key is required.

## Live check

One new request, `nba_api` 1.12.0, endpoint `scheduleleaguev2`, season `2024-25`. The player log was not requested again. It was the cached `PlayerGameLog` for player `2544`, regular season `2024-25`, retrieved `2026-10-30T04:40:00Z` (70 rows).

Schedule retrieval time was `2026-10-31T04:35:45Z`. The raw schedule stayed in a temp cache, not in Git.

Regular-season directory from that payload:

- 1,400 source rows.
- 1,225 accepted. Canonical ids are `game:` plus the source game id and `team:` plus the source team id.
- 170 `season_type_mismatch`: prefixes `001` (73), `003` (6), `004` (84), `005` (6), and `006` (1). Prefix `004` is playoffs and is accepted only when the requested season type is Playoffs.
- 5 `neutral_site`, all prefix `002`: `0022400147`, `0022400621`, `0022400633`, `0022401229`, `0022401230`.

The 1,225 accepted games are the rows that had a source game id, a UTC tip, and a tricode that matched the source team id under the current abbreviation map, and that were not neutral-site games. That count is not complete league coverage. Five regular-season games were held out as neutral site. Games with other id prefixes were held out of the regular-season directory. The endpoint is a current extract, so a missing or extra row would not be visible from the accepted count alone.

Joining the 70 cached player rows to that regular-season directory, with player `2544` mapped explicitly to `player:2544`:

- 70 accepted. 0 quarantined.
- Earliest accepted row: `game:0022400062`, `team:1610612747`, 16 points, 4 assists, 5 rebounds, 35 minutes.
- Nothing was relaxed to make the count 70. Those 70 rows matched a non-neutral regular-season game and the matchup agreed with the schedule. A neutral-site game, an unknown abbreviation, or a home/away disagreement would still be quarantined.
- That join used the log retrieval time as `available_at`. The schedule was retrieved later, on `2026-10-31T04:35:45Z`. A joined record now keeps `log_retrieved_at` as the original log retrieval time and sets `available_at` to the later of the log and schedule retrieval times. The log snapshot and the schedule snapshot stay different. In strict point-in-time mode, a cutoff before the schedule retrieval cannot use that mapping.

## What changed

`NbaApiScheduleAdapter` reads `scheduleleaguev2` once per season and builds a game directory. Regular season and playoffs are separate directories. Game ids come from `gameId`. Team ids come from `homeTeam_teamId` and `awayTeam_teamId`. The abbreviation file checks that `teamTricode` names that same id. `tip_at` is `gameDateTimeUTC` only when it is UTC. `available_at` is the retrieval time.

`config/nba-team-abbreviations.json` is the current 30-team map copied from `nba_api.stats.static.teams` in `nba_api` 1.12.0. Historical abbreviations are not in it.

Player logs can use that directory instead of a hand-written game map. The matchup must agree: `vs.` is home and `@` is away. Duplicate schedule rows with the same teams and tip keep one game. Disagreeing rows are `conflicting_game_identity` and are not imported.

The player-log cache path now includes the season type, so a playoff log cannot reuse a regular-season file.

`rules_version` remains `2026-10-29.nba-props-angles`. `model_version` remains `none`.

## Unresolved data limitations

- Current extracts cannot establish historical availability. Neither endpoint has an as-of parameter. A row retrieved now is not evidence that the same payload existed at an earlier cutoff.
- The abbreviation map is the current 30 teams only. Older tricodes such as `SEA`, `NJN`, or `NOH` quarantine as `unknown_abbreviation`.
- Player identity is still an explicit map. Game and team ids are derived; the player id is not.
- The live check validated regular season only. Playoff separation is covered offline and was not checked against a live playoff log.
- Neutral-site games are quarantined. The matchup marker is not treated as home/away there.
- Prefixes other than `002` and `004` are neither regular season nor playoffs. The one `006` row was not given a label.
- In-progress statuses are not mapped onto `scheduled` or `final`.
- Request spacing is per adapter instance.

## Remaining gaps

- No projection math, odds, injuries, rosters, or AWS deployment.
- No calibrated confidence boost and no angle coefficient.
- Flagged research for unclear defender-return context is implemented and left off.
- `available` means the input gates passed. It is not a bet recommendation.
