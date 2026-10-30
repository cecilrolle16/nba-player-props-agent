# Architecture

This repository is the contract layer for an NBA pregame points, assists, and rebounds analysis agent. It defines the records, provider boundary, configuration, and rule audit. It does not project stats, call vendors, or deploy.

## Layers

```text
config/custom-rules.json ──► rules catalog ──► deterministic audit
config/*.example.yaml ────► validated configuration
provider quote / report ──► canonical record or unavailable/skip
ProviderAdapter.fetch ────► live_integration_not_implemented
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
