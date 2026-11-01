# Build status

The project is in development. This note records what is complete, what is still unfinished, and the next step. Custom rules were not changed. No projection math, recommendations, numeric adjustments, or AWS resources were added.

## Complete

- Canonical records for schedules, rosters, player logs, team context, injuries, market snapshots, stored projections, and analysis results.
- Versioned rules catalog and deterministic audit for points, assists, and rebounds. `rules_version` is `2026-10-29.nba-props-angles`. `model_version` is `none`. Numeric hypothesis adjustments stay disabled.
- Historical player-log and schedule-identity adapters. A joined log keeps the original log retrieval time, and `available_at` is not earlier than both the log retrieval and the schedule retrieval. Strict point-in-time mode does not use a schedule mapping retrieved after the cutoff.
- Odds adapter for one event's `player_points`, `player_assists`, and `player_rebounds` markets. `captured_at` is the book update time. `available_at` is the retrieval time. Over and under quotes are stored only when they share the player, event, stat, line, sportsbook, and source update time. Missing markets stay missing. The shipped request cap is 3 credits. Authentication and rate-limit failures are not retried. Keys are redacted from recorded errors and cache files.
- Event matching. Exactly one schedule game must share the home team, away team, and season, with a UTC tip within the configurable 900-second window. The schedule tip and the odds commence time are both kept, with `tip_delta_seconds` and method `home_away_season_within_tip_tolerance`. Neither timestamp replaces the other. Pregame eligibility stops at the earlier of the two tips. Reversed home and away, more than one candidate, a different season, and a gap wider than 900 seconds are quarantined.
- Example configuration, the current 30-team abbreviation map, and the current 30-team odds name map. `.env.example` keeps `THE_ODDS_API_KEY` and the other secret fields blank.
- Explicit Odds API player map for the Thunder at Rockets slate. Each row keeps the provider spelling, the roster spelling, any reviewed alias, the canonical player and team ids, the season, and the roster retrieval time. Matching is exact after case and whitespace normalization, or an explicit alias. A name with no mapping, two players, a team outside the event, or evidence later than the odds retrieval is quarantined. Odds `captured_at`, `available_at`, and snapshot ids are not rewritten. Later identity evidence does not bypass the earlier-tip gate.

## Test results

`python -m pytest` — 101 passed in 1.42s (Python 3.12.10). No warnings. This run includes the player-map tests and did not call the network.

Automated tests use synthetic fixtures and do not call the network. No API key is required. The matching tests cover 600-second and 660-second gaps, the 901-second boundary, two candidates, reversed home and away, a different season, and post-tip rejection at the earlier tip.

## Live check

The prior events list was retrieved at `2026-10-31T23:32:27Z`. It was checked at `2026-11-01T00:12:05Z` and was not fresh, so one new events request was made. Documented cost 0. Headers: `x-requests-remaining` 500, `x-requests-used` 0, `x-requests-last` 0. Eleven events were returned. The schedule directory was the cached `2026-27` regular-season read, not a new schedule request. That accepted directory is not complete league coverage.

Six events were still pregame under the earlier tip. Five had already reached that earlier tip. None were ambiguous, reversed, a season mismatch, out of window, or an unknown team.

One event-odds request followed, for Oklahoma City Thunder at Houston Rockets, event `c30d3bdf126e98ced5b9389db8617926`, season `2026-27`, game `game:0022600153`. Schedule tip `2026-11-01T00:30:00Z`. Odds commence time `2026-11-01T00:40:00Z`. Difference 600 seconds. Method `home_away_season_within_tip_tolerance`. Markets were points, assists, and rebounds in region `us`. Documented maximum cost 3. No retry.

Odds headers: `x-requests-remaining` 497, `x-requests-used` 3, `x-requests-last` 3. This check used 3 credits. The key was not printed.

Result with an empty player map: 0 accepted, 329 quarantined. Reasons: 326 `unresolved_player_name`, 3 `unpaired_side`. Missing markets: none.

## Retrospective player identity

The cached event-odds payload was normalized again with no new Odds API request. The schedule directory was the cached `2026-27` regular-season read, retrieved `2026-10-31T23:32:35Z`. The odds retrieval clock stayed `2026-11-01T00:12:06Z`. The event still matched `game:0022600153`, schedule tip `2026-11-01T00:30:00Z`, odds commence `2026-11-01T00:40:00Z`, difference 600 seconds.

Identity evidence is `nba_api` 1.12.0 `CommonTeamRoster` for season `2026-27`, Houston `1610612745` and Oklahoma City `1610612760`, retrieved `2026-11-01T00:39:38.453334Z`. Each source row's `SEASON` field was `2026`. Player ids were cross-checked against `nba_api.stats.static.players` in that same installed package. Static players has no team id, so team membership comes from the roster response. The raw roster response is not in Git.

Fourteen provider names are mapped. Twelve match the roster spelling exactly. Two are explicit aliases: `Jabari Smith Jr` to roster `Jabari Smith Jr.` (`player:1631095`, Houston) and `Bogdan Bogdanovic` to roster `Bogdan Bogdanović` (`player:203992`, Houston). `Jaylin Williams` is `player:1631119` on Oklahoma City. `Jalen Williams`, `player:1631114`, is a different player and is not an alias.

Reprocess result: 0 accepted, 329 quarantined. No market was missing.

| Market | `identity_not_yet_available` | `unpaired_side` |
| --- | ---: | ---: |
| `player_points` | 152 | 3 |
| `player_assists` | 70 | 0 |
| `player_rebounds` | 104 | 0 |

The 326 paired quotes resolved to a reviewed player and team, then stayed quarantined because the roster evidence time is after the odds retrieval. It is also after the earlier tip, `2026-11-01T00:30:00Z`. Those quotes were not relabeled as current, and the pregame gate was not bypassed. The 3 unpaired sides are all `player_points`. The missing side was not invented. No name remained unresolved, ambiguous, or on the wrong team.

## Unfinished, in development

- Accepted quotes from the cached Thunder at Rockets response. Identity evidence was retrieved after that odds response and after the earlier tip, so the paired quotes stay `identity_not_yet_available`.
- Three unpaired `player_points` sides in that response. They were not stored as quotes.
- The 900-second quote age. It is an unvalidated operational default. This live response was not used to measure it.
- The 900-second tip window. It covered this 11-game list, where the odds commence time was 600 or 660 seconds after the schedule tip. It is not proof that every later slate uses the same offset.
- Historical availability. Current player-log and schedule extracts have no as-of parameter.
- Team coverage. Abbreviation and odds name maps are the current 30 teams only. Neutral-site games stay quarantined. The accepted schedule directory is not complete league coverage.
- A roster adapter, injuries, projections, recommendations, and AWS deployment. The Thunder and Rockets identity map is a reviewed configuration, not a roster feed.
- Calibrated confidence and angle coefficients. Flagged defender-return research stays off.
- `available` means the input gates passed. It is not a bet recommendation.

## Next step

Leave the cached Thunder at Rockets quotes non-actionable. Do not request that event from The Odds API again. A later slate can be normalized only when reviewed identity evidence exists at or before the odds retrieval and that retrieval is still before the earlier tip. Do not start projections, recommendations, or AWS.
