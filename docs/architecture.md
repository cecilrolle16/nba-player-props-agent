# Architecture

This repository is the contract layer for an NBA pregame points, assists, and rebounds analysis agent. It defines the records, provider boundary, configuration, and rule audit. It does not project stats or deploy. The base provider interface does not call the network.

## Layers

```text
config/custom-rules.json ──► rules catalog ──► deterministic audit
config/*.example.yaml ────► validated configuration
provider quote / report ──► canonical record or unavailable/skip
ProviderAdapter.fetch ────► live_integration_not_implemented
nba_api player logs ──────► PlayerGameLog or a quarantined row
odds-api event odds ──────► MarketSnapshot or a quarantined quote
```

Canonical records live in `src/contracts/`. The adapter interface lives in `src/ingestion/adapters/base.py`. Rule evaluation lives in `src/rules/`.

## Identity and time

Every accepted fact carries a canonical id, at least one source id, and a timezone-aware UTC timestamp. `available_at` is the earliest time the fact may be used. A fact whose `available_at` is after the decision time (`as_of`) returns `unavailable` / `not_yet_available`. The audit clock is `as_of`, never the wall clock.

Canonical ids look like `player:synthetic-guard-a`. Fixtures in this step use synthetic players, teams, books, and providers.

`model_version` is `none`. `rules_version` is `2026-10-29.nba-props-angles`.

## Records

| Record | Module | Notes |
| --- | --- | --- |
| Schedule | `contracts/schedule.py` | Tip time, home, away, season |
| Roster | `contracts/roster.py` | Player membership |
| Player log | `contracts/player_log.py` | Observed points, assists, rebounds |
| Team context | `contracts/team_context.py` | Null pace or rating means that metric is unavailable |
| Injury | `contracts/injury.py` | Point-in-time report |
| Market snapshot | `contracts/market.py` | Well-formed quote only |
| Projection | `contracts/projection.py` | Stored input, not a calculation |
| Analysis result | `contracts/analysis.py` | State, reason, versions, audit |

Raw quotes use `MarketQuoteInput` so a bad price can be represented and rejected. `MarketSnapshot` cannot be constructed from malformed odds.

## Provider capabilities

The only capability names are `schedules`, `rosters`, `player_logs`, `team_context`, `injuries`, `markets`, and `projections`. Anything else is `unknown_provider_capability`. A known capability the provider did not declare is `unsupported_capability`. A market outside that provider's list is `unsupported_market`.

Example providers are disabled. `ProviderAdapter.fetch` checks the declaration and then returns `live_integration_not_implemented`. It has no network client.

`NbaApiPlayerLogAdapter` calls `nba_api`'s `PlayerGameLog` endpoint for one player and one season type, caches the raw rows under `data/`, and maps them to `PlayerGameLog`. `available_at` on those records is the retrieval time. The endpoint has no as-of parameter, so the result is marked point-in-time limited. Unmapped ids, DNP rows, missing counting stats, same-day or later games, and duplicate games are quarantined instead of being coerced into a played line. `fetch("player_logs")` without a player, season, and cutoff does not call the network.

`NbaApiScheduleAdapter` calls `scheduleleaguev2` once per season. Canonical game ids are `game:` plus the source game id. Canonical team ids are `team:` plus the source team id. A maintained abbreviation map checks that the tricode and the team id name the same team. Regular-season ids start with `002` and playoff ids start with `004`. A player-log matchup is accepted only when those sides agree with the schedule row. `tip_at` is the source UTC tip. Schedule `available_at` is the schedule retrieval time, and its snapshot id is separate from the player-log snapshot.

A current extract cannot establish that the same payload was available at an earlier cutoff. On a joined record, `log_retrieved_at` stays the player-log retrieval time. `available_at` is not earlier than both that time and the schedule retrieval time. In strict point-in-time mode, a cutoff before the schedule retrieval does not use that later mapping.

`OddsApiMarketAdapter` calls The Odds API v4 events list and then one event-odds request for `player_points`, `player_assists`, and `player_rebounds`. The documented events cost is 0. The documented event-odds maximum is unique requested markets times region units, and the shipped cap is 3 credits. `captured_at` is the market `last_update`. `available_at` is the retrieval time. An over and under are stored only when they share the player, event, stat, line, sportsbook, and source update time. Player ids come from an explicit name map that also records team, season, and the time the identity evidence was retrieved. Matching is exact after case and whitespace normalization, or an explicit reviewed alias. Evidence retrieved after the odds response does not rewrite the odds clocks and does not bypass the earlier-tip gate. The event must match exactly one schedule game with the same home team, away team, and season, and a UTC tip within 900 seconds. The schedule tip and the odds commence time are both kept, with their difference and the matching method. Neither timestamp replaces the other. A quote is post-tip at the earlier of those two times. On the 2026-10-31 events extract, the odds commence time was 600 or 660 seconds after the schedule tip for the same teams. Quotes after the unvalidated 900-second age, or with a source update later than retrieval, are quarantined. `fetch("markets")` without an event id does not call the network.

## States

| State | Meaning |
| --- | --- |
| `available` | Inputs passed the blocking rules. No projection was produced. |
| `unavailable` | The input is missing, malformed, unsupported, or not yet knowable. |
| `skip` | The player is identified, but a supplied injury report is older than its freshness window. |

The primary reason is the matched blocking rule with the lowest precedence. Every rule is still recorded, matched or not, in that same order.

## Input gates

These gates decide whether the whole analysis is actionable. They are separate from the three angle policies.

| Precedence | Gate | Effect |
| --- | --- | --- |
| 10 | `unknown_provider_capability` | unavailable |
| 12 | `unsupported_capability` | unavailable |
| 15 | `not_yet_available` | unavailable |
| 20 | `malformed_odds` | unavailable |
| 30 | `unresolved_player_id` | unavailable |
| 40 | `stale_injury_report` | skip. The window is configurable. 180 minutes is an unvalidated operational default |
| 45 | `missing_essential_evidence` | unavailable when availability or minutes evidence is missing |
| 50 | `missing_historical_projections` | unavailable when history is required |

`skip` here means the whole analysis abstains. Skipping one angle does not.

## Angle policies

Every evaluation records all three policies, including when a gate blocks the analysis. Each angle audit has a status, reason, evidence snapshot ids, an adjustment amount, and a validation status. Magnitudes stay null. `numeric_hypothesis_adjustments_enabled` is false.

| Policy | Markets | Result when the facts are missing |
| --- | --- | --- |
| `defender_return` | points only | angle status `skipped`. Assists and rebounds are `not_applicable` |
| `no_streak_chasing` | points, assists, rebounds | angle status `unsupported` when the only support is the streak or a correlated indicator |
| `underperformance_rebound` | points, assists, rebounds | angle status `skipped` when three contemporaneous pregame misses, stable volume, or exclusion evidence is missing |

Injury news, a minutes change, or a role change sets the angle status to `overridden`. Verified defender-return facts with unclear minutes or rotation evidence become `flagged_research` only when that option is turned on. The shipped configuration leaves it off. Normal minutes plus healthy defensive performance set the defender angle to `reduced` and still apply no number.

Pace, usage, and rest are future context models. They are not custom-angle adjustments.

A historical projection counts toward a rebound only when it was available at or before that game's tip and is not today's projection.

## Configuration

`config/providers.example.yaml` and `config/schedules.example.yaml` are validated example files. Schedules run in UTC. `.env.example` points at those files and keeps credential values empty. `load_foundation` reports one `available` or `unavailable` status for the shipped set.

## Audit

`rules/audit.py` hashes the rule fingerprint, the ordered firings, the adjustment refusals, and the subject. The same subject and catalog always produce the same `audit_id`.

## Out of scope

Live odds or injury clients, projection math, bankroll sizing, bet placement, and AWS deployment are not part of this step.
