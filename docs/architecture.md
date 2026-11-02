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
- Calculations do not call an LLM. Rules, implied probability, edge, and EV are deterministic functions of stored records. Amazon Bedrock may explain a finished report. It is not on that calculation path, and a missing model does not change the numbers.
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
| Features | Historical box scores implemented | Accepted logs joined to schedule tips, cutoff, lookback | Versioned averages, rates, variability, and included or excluded games | Missing stats, DNP, zero minutes, leakage, insufficient history | Data quality |
| Projections | Contract only. No calculator | Feature rows, or a stored `Projection` supplied as an input | Stored `Projection` with `mean` | No historical projection when history is required | Features, or an external stored projection |
| Custom rules | Implemented | `AnalysisSubject` and `config/custom-rules.json` | `AnalysisResult` and a stable `audit_id` | Any matched blocking gate; an enabled numeric adjustment | Data quality |
| Market comparison | Planned | Paired and unpaired `MarketSnapshot` rows | Comparable prices at the same line; no invented side | Unpaired side, mismatched line, incomparable books | Ingestion |
| Decisions | Planned | Rules result, projection, compared prices | Pass or a candidate with edge and EV | Gates failed, identity later than the quote, threshold unset | Rules, projections, market comparison |
| Reporting | Implemented as an optional Bedrock explanation | Finished Python report, audits, source ids, risks, and data gaps | An explanation linked to the unchanged report, or a deterministic fallback | Invalid model output, timeout, or Bedrock disabled | Decisions. Not required for the numerical report |
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
Python report ────────────► optional Bedrock explanation or a deterministic fallback
```

`nba_api` and The Odds API stop at canonical records. Validation and the numerical stages run without Bedrock. `pipeline.run_pipeline` attaches a Bedrock explanation after the research decision. The shipped config leaves the client disabled, so that attachment is the deterministic fallback and does not call AWS. A gitignored `config/bedrock.local.yaml` can set `enabled: true` for this machine. Bedrock sees the finished report and the source ids already on it. Provider text is wrapped as untrusted data. Bedrock cannot change a number, a gate, a synthetic label, or an abstention, and it cannot turn `live_recommendation` on.

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

## Bedrock explanation

`reasoning.bedrock.explain_report` runs after the Python report. The shipped file `config/bedrock.example.yaml` has `enabled: false`, so the default path does not call AWS. The client is boto3 Bedrock Runtime `converse`, with the region and model or inference-profile id taken from that file. Credentials come from the AWS credential chain or an IAM role. The example policy is `config/bedrock-iam-policy.example.json`. It allows `bedrock:InvokeModel` and `bedrock:GetInferenceProfile` for one profile, and `bedrock:InvokeModel` for that profile's foundation model only when the request uses the profile. The example model is the US Anthropic Claude Haiku 4.5 inference profile. `GetInferenceProfile` for that profile lists destination models in us-east-1, us-east-2, and us-west-2. Claude 3 Haiku is end of life and is not the configured model. This repository does not grant access to the profile.

Timeouts, retries, and output length are bounded by the config. If Bedrock is disabled, times out, or returns a reference, number, label, or result that does not match the Python report, the structured report is kept and the explanation is a deterministic fallback. A model response is not a bet.

## Planned stages

Historical features, the baseline mean, the integer histogram, side probabilities, the price, and the research decision are calculated. Settlement grading, bankroll sizing, and AWS job orchestration are not.

**Features.** Implemented in `features/historical.py`. One snapshot per player, game, and market uses only completed games whose tip is before the target and whose schedule and log evidence were available at or before `as_of`. Games are ordered by tip time. The target game is excluded. DNP, zero minutes, and missing stats are excluded with a reason and are not stored as zeros. Sample variability is the sample standard deviation with divisor `n - 1`, and it is null when fewer than two games are included. The shipped lookback is unvalidated. Usage, shot volume, pace, defense, and lineup stay null. Historical minutes are not expected future minutes.

**Projections.** `projection/baseline.py` stores a baseline mean: expected minutes times a historical per-minute rate. Shrinkage runs only when an explicit prior is supplied. The shipped setting is unshrunk and does not invent a prior. `outcomes` stays empty. The baseline `model_version` is `2026-11-01.baseline-minutes-rate`, and its calibration status is `not_applicable`. The rules catalog `model_version` stays `none`, and the rules engine still accepts a `Projection` only as a stored historical input. Today's projection cannot count as one of the three historical misses.

**Market comparison.** A supplied integer histogram is classified at the quote's line and side. A separate price record stores implied probability, no-vig, probability edge, and EV for that same quote. The comparison does not select a best price across books, and it does not invent the other side of an unpaired quote.

**Decisions.** The research decision is `research_candidate`, `no_bet`, or `unavailable`. The legacy `pass` or `abstain` record is input eligibility only. Edge and EV stay on the price record. No stake is attached. The formulas are in `docs/decision-policy.md`.

**Reporting.** `explain_report` may restate the finished report. It does not change a number. With Bedrock disabled, the explanation is the deterministic fallback.

**AWS.** Job order, invalidation, retries, quotas, and monitors are in `docs/build-roadmap.md`. No infrastructure is in this repository. The Bedrock client is not that job runner.

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

`pipeline.run_pipeline` is that path. When the rules state is `available`, the feature stage calculates the historical box score and the projection stage may store a baseline mean. If the bundle also supplies an integer residual histogram, the comparison stage classifies it at the quote line and side, and a separate price record stores implied probability, no-vig, probability edge, and expected value. That price is experimental and uncalibrated. The legacy decision stays `pass` / `not_implemented` and does not copy the edge or the EV. A separate research decision may be `research_candidate`, `no_bet`, or `unavailable`. It has no stake. Without that histogram, comparison and decision stay `not_implemented` and do not invent a probability, edge, or EV. An ineligible subject leaves the feature stage `unavailable`. Insufficient history or missing expected minutes leaves the projection unavailable and stores no mean. Tests may inject synthetic stage values; those values are labeled synthetic and uncalibrated, and `live_recommendation` stays false. `AnalysisState.available` means the input gates passed. The report recommendation is `none`. Settlement records exist as a contract and are not produced by the pipeline. No provider is called.

The baseline stat record names a player, game, and market. It has no line and no side. Win, loss, and push probabilities from the integer histogram sit on a comparison that names the line, side, and odds snapshot. A synthetic comparison also names the book. Several comparisons may cite one distribution. The pipeline report carries the one comparison for the quote in the bundle.

## Framework checklist

| Component | State |
| --- | --- |
| Input gates and three angle audits | Complete. `available` is eligibility only |
| Historical box-score features | Complete. Averages, per-minute rates, and sample variability |
| Baseline mean | Complete. Expected minutes times the selected per-minute rate. The mean is not the histogram |
| Stat distribution contract | The baseline record keeps `outcomes` empty. The integer histogram is a separate experimental record |
| Comparison identity: player, event, stat, line, side, book, odds snapshot | A synthetic comparison names the book and the American odds. A residual comparison names the line, side, and snapshot and does not select a price |
| Side probabilities on that comparison | Calculated for the integer histogram only. Uncalibrated. A split tail is unavailable |
| Implied probability and no-vig price | Calculated on `PriceComparison`. Raw implied contains vig. No-vig is not a true probability. The comparison fields stay null |
| Decision edge and EV | Calculated on the price record. The research decision references that record and does not copy the numbers |
| Threshold, stake, bankroll sizing | Shipped research minima are unset. Stake and bankroll stay null. A labeled synthetic test may supply minima |
| `live_recommendation` | Fixed false |
| Settlement win, loss, push, and void | Contract only. Push and void are different outcomes. `unit_profit` is fixed null and `graded` is false. Temporary restriction on profit. Grading is unimplemented |
| Stage protocols on `PipelineStages` | Extension points. Features, the baseline mean, side probabilities, a residual price, and the research policy calculate. The legacy decision does not become a recommendation |
| Provider adapters for logs, schedule, roster, and one-event odds | Complete and unchanged by this skeleton |
| Injury adapter, team-context adapter, AWS jobs | Unimplemented. The integer histogram is an uncalibrated experiment, not a general outcome model |
| Bedrock explanation | Optional and disabled in the shipped config. It explains a finished report or falls back. It does not calculate |

## Configuration

`load_foundation` checks the shipped example files together: providers, schedules, env example, log settings, feature windows, baseline projection settings, roster settings, schedule settings, team abbreviations, odds settings, odds team names, and the player map. Schedules run in UTC. `.env.example` keeps credential values empty. A filled secret in the example file is `secret_present`.
