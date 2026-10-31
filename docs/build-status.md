# Build status

The project is in development. This note records what is complete, what is still unfinished, and the next step. Custom rules were not changed. No projection math, recommendations, numeric adjustments, or AWS resources were added.

## Complete

- Canonical records for schedules, rosters, player logs, team context, injuries, market snapshots, stored projections, and analysis results.
- Versioned rules catalog and deterministic audit for points, assists, and rebounds. `rules_version` is `2026-10-29.nba-props-angles`. `model_version` is `none`. Numeric hypothesis adjustments stay disabled.
- Historical player-log and schedule-identity adapters. A joined log keeps the original log retrieval time, and `available_at` is not earlier than both the log retrieval and the schedule retrieval. Strict point-in-time mode does not use a schedule mapping retrieved after the cutoff.
- Odds adapter for one event's `player_points`, `player_assists`, and `player_rebounds` markets. `captured_at` is the book update time. `available_at` is the retrieval time. Over and under quotes are stored only when they share the player, event, stat, line, sportsbook, and source update time. Missing markets stay missing. The shipped request cap is 3 credits. Authentication and rate-limit failures are not retried. Keys are redacted from recorded errors and cache files.
- Example configuration, the current 30-team abbreviation map, and the current 30-team odds name map. `.env.example` keeps `THE_ODDS_API_KEY` and the other secret fields blank.

## Test results

`python -m pytest` — 91 passed in 0.78s (Python 3.12.10). No warnings.

Automated tests use synthetic fixtures and do not call the network. No API key is required.

## Live check

Documented V4 costs, checked before any paid call: the events list costs 0. One event-odds request for three markets and one region costs at most 3.

One events request for `basketball_nba` returned `x-requests-remaining` 500, `x-requests-used` 0, and `x-requests-last` 0. The key was not printed. The response was cached outside the repository.

The events payload had 11 games. Team names matched the current 30-team name file. Nine commence times were still in the future. A `2026-27` regular-season schedule read accepted 1,197 games and quarantined 77. That accepted count is not complete league coverage.

No odds request was sent. None of the 11 events matched one schedule game on home team, away team, and the same UTC tip. The closest same-team tips differed by 600 or 660 seconds.

## Unfinished, in development

- Live odds quotes. The paid event-odds call has not been made, so accepted quotes, quarantined quotes, and missing markets have not been observed.
- Tip identity. The adapter does not treat a 600-second or 660-second gap as the same game.
- Player identity for odds. Names require an explicit map. The live check did not supply one.
- The 900-second quote age. It is an unvalidated operational default and was not measured against a live odds payload.
- Historical availability. Current player-log and schedule extracts have no as-of parameter.
- Team coverage. Abbreviation and odds name maps are the current 30 teams only. Neutral-site games stay quarantined. The accepted schedule directory is not complete league coverage.
- Rosters, injuries, projections, recommendations, and AWS deployment.
- Calibrated confidence and angle coefficients. Flagged defender-return research stays off.
- `available` means the input gates passed. It is not a bet recommendation.

## Next step

Decide whether an Odds API commence time and a schedule tip that differ by 600 or 660 seconds are the same game. Then make one event-odds request for one matched upcoming game, three markets, and one region, with the 3-credit cap. Do not start projections, recommendations, or AWS in that step.
