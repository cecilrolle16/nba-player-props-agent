# Architecture

This repository is an in-development pregame agent for NBA points, assists, and rebounds. Code fetches and stores facts. A later model may explain those facts. The model is not a source for odds, injuries, stats, or probabilities. The user places any bet. Nothing here claims accuracy, profitability, or a deployment.

The blueprint is split on purpose:

| Document | Role |
| --- | --- |
| `docs/architecture.md` | Stage order, dependencies, and what exists today |
| `docs/data-contracts.md` | Records, provenance, versioning, and missing fields |
| `docs/decision-policy.md` | Gates, custom policies, edge, EV, and the decision boundary |
| `docs/evaluation-plan.md` | Settlement, calibration, and what may not be claimed |
| `docs/build-roadmap.md` | Bounded phases and acceptance criteria |
| `docs/build-status.md` | Evidence from the repository and the last live checks |

Status words mean the same thing in every document. **Implemented** is present in `src/` or `config/` and covered by the offline tests. **Planned** is specified here and is not in the code. **Unresolved** is a choice this blueprint refuses to invent.

## Principles

- Providers are replaceable. Canonical records do not contain a vendor URL or a credential. `ProviderAdapter.fetch` refuses a live call unless a specific adapter is given an explicit query.
- Calculations do not call an LLM. Rules, implied probability, edge, and EV are deterministic functions of stored records.
- `as_of` is the decision clock. `available_at` is the earliest time a fact may be used. A fact later than `as_of` is `not_yet_available`.
- `model_version` is `none`. `rules_version` is `2026-10-29.nba-props-angles`. The catalog rejects any other model version and rejects `numeric_hypothesis_adjustments_enabled: true`.
- `available` means the input gates passed. It is not a recommendation, a stake, or a claim that a side will win.
- Raw downloaded payloads stay in gitignored `data/`. Published configuration is reviewed mappings and synthetic fixtures only.

## Stage map

Each stage names its inputs, outputs, failure states, and what must already exist.

| Stage | Status | Inputs | Outputs | Failure | Depends on |
| --- | --- | --- | --- | --- | --- |
| Ingestion | Implemented for logs, schedule, one-team rosters, and one-event odds | Provider payload, explicit ids, retrieval clock | Canonical record or a quarantine reason | Malformed payload, unknown identity, stale or post-tip quote, provider error | Provider declaration |
| Data quality | Implemented as quarantine plus the rules gates | Canonical records and `as_of` | `available`, `unavailable`, or `skip`, with a reason | See the gate table in `docs/decision-policy.md` | Ingestion |
| Features | Planned | Accepted logs, schedule, roster, team context | Versioned feature rows for one player, game, and market | Missing minutes, unknown opponent, feature time after `as_of` | Data quality |
| Projections | Contract only. No calculator | Feature rows, or a stored `Projection` supplied as an input | Stored `Projection` with `mean` | No historical projection when history is required | Features, or an external stored projection |
| Custom rules | Implemented | `AnalysisSubject` and `config/custom-rules.json` | `AnalysisResult` and a stable `audit_id` | Any matched blocking gate; an enabled numeric adjustment | Data quality |
| Market comparison | Planned | Paired and unpaired `MarketSnapshot` rows | Comparable prices at the same line; no invented side | Unpaired side, mismatched line, incomparable books | Ingestion |
| Decisions | Planned | Rules result, projection, compared prices | Pass or a candidate with edge and EV | Gates failed, identity later than the quote, threshold unset | Rules, projections, market comparison |
| Reporting | Planned | Decision, audit, quarantine counts | A slate report that restates reasons | Missing audit id | Decisions |
| Evaluation | Specified, not implemented | Pregame decision plus a final log | Win, loss, push, or void; later, calibration | Game not final, log missing, quote was never accepted | Settlement log |
| AWS orchestration | Planned | Job config, caches, quotas | Ordered jobs, retries, and monitors | Quota, auth, or a failed prior job | All prior stages for a live slate |

## Implemented path

```text
config/custom-rules.json ──► rules catalog ──► deterministic audit
config/*.example.yaml ────► validated configuration
provider row ─────────────► canonical record or quarantine
ProviderAdapter.fetch ────► live_integration_not_implemented
nba_api player logs ──────► PlayerGameLog or a quarantined row
nba_api schedule ─────────► ScheduleGame or a quarantined game
nba_api roster ───────────► RosterEntry or a quarantined player
odds-api event odds ──────► MarketSnapshot or a quarantined quote
AnalysisSubject ──────────► AnalysisResult
```

Canonical records live in `src/contracts/`. Adapters live in `src/ingestion/adapters/`. Rule evaluation lives in `src/rules/`.

### Identity and time

Every accepted fact carries a canonical id, at least one source id, and a timezone-aware UTC timestamp. Canonical id patterns are `player:`, `team:`, `game:`, `snapshot:`, and `projection:`, each followed by a lowercase token. Fixtures use synthetic ids. Live NBA ids are the source digits with that prefix, for example `player:1631119` and `game:0022600153`.

`captured_at` on a market snapshot is the book update time. `available_at` is the retrieval time. Those two clocks are not interchangeable. On a joined player log, `log_retrieved_at` stays the log retrieval, and `available_at` is not earlier than both the log retrieval and the schedule retrieval. Strict point-in-time mode does not use a schedule directory retrieved after the cutoff.

`nba_api` schedule, roster, and player-log endpoints have no as-of parameter. A current extract cannot establish that the same payload was available at an earlier cutoff. The result is marked point-in-time limited.

### Providers

The only capability names are `schedules`, `rosters`, `player_logs`, `team_context`, `injuries`, `markets`, and `projections`. Anything else is `unknown_provider_capability`. A known capability the provider did not declare is `unsupported_capability`. A market outside that provider's list is `unsupported_market`.

Example providers in `config/providers.example.yaml` are `enabled: false`. An enabled provider is accepted only for an implemented capability set. Today those sets are nba-api player logs, nba-api logs plus schedules, nba-api rosters, nba-api logs plus schedules plus rosters, and Odds API markets.

`NbaApiPlayerLogAdapter` reads `PlayerGameLog` for one player and one season type. Unmapped ids, DNP rows, missing counting stats, same-day or later games, and duplicate games are quarantined. `fetch("player_logs")` without a player, season, and cutoff does not call the network.

`NbaApiScheduleAdapter` reads `scheduleleaguev2` once per season. Regular-season game ids start with `002` and playoff ids start with `004`. A tricode must agree with the team id in `config/nba-team-abbreviations.json`. Neutral-site games are quarantined. The accepted directory is not complete league coverage. The map is the current 30 teams. Historical tricodes are unknown.

`NbaApiRosterAdapter` reads `commonteamroster` for one team and one season. The display name is stored as returned. Supplemental status values 0 and 1 are both roster membership. Neither value is an injury flag. The adapter does not resolve Odds API spellings.

`OddsApiMarketAdapter` reads The Odds API v4 events list and then one event-odds request for `player_points`, `player_assists`, and `player_rebounds`. The documented events cost is 0. The documented event-odds maximum is unique requested markets times region units. The shipped cap is 3 credits. Authentication and rate-limit failures are not retried. Keys are redacted from errors and cache files. Over and under are stored only when they share the player, event, stat, line, sportsbook, and source update time. A missing market stays missing. A missing side stays missing.

An odds event matches exactly one schedule game with the same home team, away team, and season, and a UTC tip within 900 seconds. Both tip times and `tip_delta_seconds` are kept. Method is `home_away_season_within_tip_tolerance`. Pregame eligibility stops at the earlier tip. The 900-second window and the 900-second quote-age limit are unvalidated operational defaults.

Player names on odds resolve only through `config/odds-player-map.json`. Matching is exact after case and whitespace normalization, or an explicit reviewed alias. Fuzzy matching is not used. Evidence retrieved after the odds response does not rewrite odds clocks and does not bypass the earlier-tip gate.

Team context and injury reports have contracts and no adapters. Stored projections have a contract and no calculator.

### Rules

`rules/engine.py` evaluates every catalog rule in precedence order and still records the rules that do not match. The primary reason is the matched blocking rule with the lowest precedence. Angle policies are evaluated even when a gate blocks the analysis. Skipping one angle does not abstain on the whole analysis. `skip` from `stale_injury_report` does.

`rules/audit.py` hashes the rule fingerprint, the ordered firings, the adjustment refusals, and the subject. The same subject and catalog always produce the same `audit_id`.

Pace, usage, and rest are named future context models. A requested adjustment for them is recorded with amount null and validation status `not_modeled`. It is not a custom-angle adjustment.

## Planned stages

These stages are specified so later code has a boundary. They are not implemented.

**Features.** One row per player, game, and market, built only from facts with `available_at <= as_of`. The row carries the snapshot ids it used. A missing minutes or availability input is a failed feature, not a zero.

**Projections.** A later calculator may write a `Projection`. Until `model_version` changes, the engine accepts a projection only as a stored historical input. Today's projection cannot count as one of the three historical misses.

**Market comparison.** Group accepted snapshots by game, player, market, and line. Select the best American price within one line. Do not mix lines. Do not invent the other side of an unpaired quote.

**Decisions.** A candidate exists only after the rules state is `available`, the quote is still pregame at the earlier tip, identity evidence was available at or before the odds retrieval, and a projection supplies a hit probability. Otherwise the output is pass. Edge and EV use the formulas in `docs/decision-policy.md`. No stake is attached.

**Reporting.** Restate the audit id, gate reason, angle statuses, quarantine counts, and the candidate or pass. The report does not change a number.

**AWS.** Job order, invalidation, retries, quotas, and monitors are in `docs/build-roadmap.md`. No infrastructure is in this repository.

## Synthetic end-to-end path

The offline path uses records and fixtures already in the repository. It does not open a socket, read `.env`, or call a provider.

1. Load `config/custom-rules.json` through the rules catalog.
2. Build a `ScheduleGame`, a `RosterEntry`, and a `PlayerGameLog` with synthetic ids and UTC timestamps. The log's `available_at` is not earlier than the schedule retrieval used to identify the game.
3. Build a `MarketQuoteInput` that can pass `quote_problems`, or start from `tests/fixtures/odds_api_event_odds.json` after explicit player and team maps.
4. Set `as_of` after every fact's `available_at` and `captured_at`, and before `tip_at`.
5. Supply availability and minutes evidence. History is required unless the subject sets `historical_required` false. When it is required, supply one historical `Projection` for the same player, game, and market, with `projected_at` and `available_at` at or before `as_of`.
6. Call `rules.engine.evaluate`.
7. Expect `state` `available` and reason `inputs_accepted` only on that complete subject. Expect three angle audits, null adjustment amounts, and `model_version` `none`.
8. Move one timestamp after `as_of`, remove minutes, or break the player id, and expect `unavailable` or `skip` with the matching gate. The angle audits are still present.

`pipeline.run_pipeline` is that path. The default stages after `evaluate` return `not_implemented` or `unavailable` and do not invent a probability, edge, or EV. Tests may inject synthetic stage values; those values are labeled synthetic and uncalibrated, and `live_recommendation` stays false. `AnalysisState.available` means the input gates passed. The report recommendation is `none`. Settlement records exist as a contract and are not produced by the pipeline. No provider is called.

The stat distribution names a player, game, and market. It has no line and no side. Win, loss, and push probabilities sit on a comparison that also names the line, side, book, and odds snapshot. Several comparisons may cite one distribution. The pipeline report carries the one comparison for the quote in the bundle.

## Framework checklist

| Component | State |
| --- | --- |
| Input gates and three angle audits | Complete. `available` is eligibility only |
| Feature snapshot references and clocks | Complete. No feature math |
| Stat distribution contract | Complete. `outcomes` is the extension field. The default leaves it empty |
| Comparison identity: player, event, stat, line, side, book, odds snapshot | Complete when a synthetic or later stage fills the price. The default does not select a price |
| Side probabilities on that comparison | Fields exist. Nothing calculates them. They cannot be stored without the line, side, book, odds, and snapshot clocks |
| Implied probability and no-vig price | Fixed null. Temporary restriction |
| Decision edge and EV | Extension fields an injected stage may fill. Nothing calculates them |
| Threshold, stake, bankroll sizing | Fixed null. Temporary restriction |
| `live_recommendation` | Fixed false |
| Settlement win, loss, push, and void | Contract only. Push and void are different outcomes. `unit_profit` is fixed null and `graded` is false. Temporary restriction on profit. Grading is unimplemented |
| Stage protocols on `PipelineStages` | Extension points. The defaults do not calculate |
| Provider adapters for logs, schedule, roster, and one-event odds | Complete and unchanged by this skeleton |
| Injury adapter, team-context adapter, projection math, AWS | Unimplemented |

## Configuration

`load_foundation` checks the shipped example files together: providers, schedules, env example, log settings, roster settings, schedule settings, team abbreviations, odds settings, odds team names, and the player map. Schedules run in UTC. `.env.example` keeps credential values empty. A filled secret in the example file is `secret_present`.
