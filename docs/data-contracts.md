# Data contracts

Records in `src/contracts/` are strict: unknown fields are rejected, instances are frozen, and timestamps are timezone-aware UTC with offset zero. This file lists the fields that exist, the provenance rules the adapters already follow, and the fields that are still missing. Missing fields are not added by this document.

## Versioning

| Name | Current value | Where it lives |
| --- | --- | --- |
| `rules_version` | `2026-10-29.nba-props-angles` | `config/custom-rules.json`, copied onto `AnalysisResult` and `Projection` |
| `model_version` | `none` | Same files. The catalog rejects any other value |
| `schema_version` | `2` on the rules file | Rules file only |
| `source_sha256` | SHA-256 of the rules file bytes | Computed when the catalog loads. Stored on the audit as `rules_fingerprint` |
| `audit_id` | SHA-256 of the audit body plus the subject JSON | `rules/audit.py` |
| Cache `version` | `1` | Adapter cache wrappers under gitignored `data/` |

A rules change requires a new `rules_version`. A projection calculator, when one exists, requires a new `model_version` and a catalog change. Old snapshots are not rewritten to the new version.

## Provenance

- `source_ids` contains at least one `provider_id` and `native_id` on every accepted canonical record that requires sources.
- `snapshot_id` identifies one stored observation. Market snapshots, schedule games, and the player-log wrapper use it. Reusing a snapshot id for a different payload is a defect.
- `available_at` is the earliest time the fact may be used. It is the retrieval time unless a join must wait for a later required source.
- `captured_at` is the provider's update time for a quote. It is not replaced by retrieval time, tip time, or identity time.
- `reported_at` on an injury is the source report time. `available_at` is when this system could read it.
- `projected_at` is when a stored projection was produced. `available_at` is when it could be used. A historical projection counts only when both are at or before `as_of`, and when `projected_at` is at or before that game's tip.
- Joined logs keep `log_retrieved_at` on `RetrievedPlayerLog`. The canonical `PlayerGameLog.available_at` is the later of the log retrieval and the schedule retrieval.
- Odds identity evidence has its own `available_at` on the player map. If that time is after the odds retrieval, the quote is `identity_not_yet_available` and no snapshot is emitted.
- Cache hits return the stored `retrieved_at`. They do not stamp the cache read time onto old rows.
- Cutoff enforcement uses `as_of` or the log cutoff. Strict point-in-time mode quarantines a schedule mapping when the cutoff is earlier than `directory.retrieved_at`.

## Implemented records

### ScheduleGame — `contracts/schedule.py`

Implemented. Adapter: `NbaApiScheduleAdapter`.

| Field | Meaning |
| --- | --- |
| `canonical_game_id` | `game:` plus the source game id |
| `snapshot_id` | `snapshot:nba-sched-` plus the game id |
| `season` | `YYYY-YY` |
| `home_team_id`, `away_team_id` | Canonical team ids. They must differ |
| `status` | `scheduled`, `postponed`, or `final` |
| `tip_at` | Source UTC tip |
| `available_at` | Schedule retrieval time |
| `source_ids` | Provider `nba-api` and the schedule native id |

The match wrapper `EventMatch` is not a canonical contract. It stores the matching method, season, both tip times, the signed tip delta, and the canonical game id. Neither tip replaces the other.

### RosterEntry — `contracts/roster.py`

Implemented. Adapter: `NbaApiRosterAdapter`.

| Field | Meaning |
| --- | --- |
| `canonical_player_id` | `player:` plus the source player id |
| `canonical_team_id` | `team:` plus the source team id |
| `season` | Requested season, including when the source row stores only the start year |
| `display_name` | Source spelling, after outer whitespace is removed |
| `active` | True for a listed roster member. Supplemental status is not copied onto this field |
| `available_at` | Roster retrieval time |
| `source_ids` | `commonteamroster:{season}:{team}:{player}` |

No `snapshot_id` field exists on this record.

### PlayerGameLog — `contracts/player_log.py`

Implemented. Adapter: `NbaApiPlayerLogAdapter`. The wrapper `RetrievedPlayerLog` adds `snapshot_id`, `schedule_snapshot_id`, and `log_retrieved_at`.

| Field | Meaning |
| --- | --- |
| `canonical_player_id`, `canonical_game_id`, `canonical_team_id` | Explicit identity map plus schedule agreement |
| `points`, `assists`, `rebounds` | Observed non-negative integers |
| `minutes` | Parsed minutes. A DNP is not stored as a played zero unless the source says the player played zero minutes |
| `available_at` | Not earlier than log and schedule retrieval |
| `source_ids` | Log source id |

The canonical log has no opponent, home flag, season, or snapshot id. Those stay on the schedule record and the wrapper.

### TeamContext — `contracts/team_context.py`

Contract only. No adapter.

`canonical_team_id`, `canonical_game_id`, `opponent_team_id`, `is_home`, optional `pace`, optional `defensive_rating`, optional `snapshot_id`, `available_at`, `source_ids`. The opponent must be a different team. A null pace or rating is `unavailable` from `metric_state`. It is not zero.

### InjuryReport — `contracts/injury.py`

Contract only. No adapter.

`canonical_player_id`, `canonical_team_id`, `status` (`out`, `doubtful`, `questionable`, `probable`, `available`), `detail`, `reported_at`, `available_at`, `source_ids`. No snapshot id. No body part, return date, or minutes limit. Those facts, when supplied to the rules, use the evidence records below.

### MarketQuoteInput and MarketSnapshot — `contracts/market.py`

`MarketQuoteInput` is the raw quote and may be incomplete. `quote_problems` rejects a bad side, non-positive line, American odds outside `^[+-](?:100|[1-9]\d{2,5})$`, an unknown market, a bad snapshot or game id, a blank book, missing source ids, or a market that disagrees with the subject.

`MarketSnapshot` is the accepted quote: snapshot id, game id, player id, `raw_player_name`, market, side (`over` or `under`), positive line, American odds, book, `captured_at`, `available_at`, and source ids. The Odds API adapter writes one snapshot per accepted side. It does not store a pair id.

### Projection — `contracts/projection.py`

Stored input. No calculator.

`canonical_projection_id`, player id, game id, market, optional finite `mean`, `is_historical`, `model_version`, `rules_version`, optional `snapshot_id`, `projected_at`, `available_at`, `source_ids`. There is no distribution, hit probability, or closing line.

### Evidence — `contracts/evidence.py`

Implemented as rule inputs, not as provider adapters.

| Record | Role |
| --- | --- |
| `AvailabilityEvidence` | Whether status is known, plus an injury-news flag |
| `MinutesEvidence` | Known expected minutes, change, and restriction |
| `RoleEvidence` | Whether the role changed |
| `DefenderReturnEvidence` | Return date, games and minutes since return, key defender, rotation, normal minutes, healthy defense |
| `StreakEvidence` | Consecutive successes and independent, correlated, or absent marks |
| `ProjectionMiss` | One historical miss with both the projection time and the game tip |
| `VolumeStability` | Minutes, usage, and shot volume, each true, false, or unknown |
| `UnderperformanceEvidence` | Misses plus cause states `excluded`, `present`, `uncertain`, or `missing` |

### Analysis — `contracts/analysis.py`

Implemented.

`AnalysisSubject` is one pregame question: `as_of`, market, raw snapshot, optional canonical player and game, optional injury and evidence, capability declaration, historical projections, `historical_required`, and requested adjustments.

`AnalysisResult` copies the decision state, reason, ids, versions, `available_at` equal to `as_of`, essential gaps, disclosures, refused adjustments, future-context notes, and the `RuleAudit`. `AngleAudit.adjustment_amount` is always null. `validation_status` is `disabled` or `unvalidated`. `AdjustmentDecision.applied` is always false and `delta` is always null.

## Odds quarantine reasons already produced

These are adapter reasons, not catalog rules. The catalog still says `unresolved_player_id` when a subject reaches the engine without a canonical player id.

`invalid_player_map`, `ambiguous_player_name`, `conflicting_player_identity`, `unresolved_player_name`, `wrong_team`, `identity_not_yet_available`, `season_mismatch`, `unknown_team`, `event_mismatch`, `ambiguous_event`, `reversed_home_away`, `post_tip`, `missing_market`, `mismatched_line`, `unpaired_side`, `duplicate_outcome`, `incompatible_timestamps`, `future_dated`, `stale`, `invalid_odds`, `malformed_payload`, `malformed_timestamp`.

Schedule quarantine includes `season_type_mismatch`, `neutral_site`, `unknown_abbreviation`, `team_conflict`, and `missing_tip_time`. Roster quarantine includes `team_mismatch`, `league_mismatch`, `duplicate_player`, `missing_name`, and `unknown_roster_status`. Log quarantine includes unmapped identity, DNP, missing stats, and duplicate games.

## Missing contracts and fields

Do not treat this list as implemented.

| Gap | Why it is missing |
| --- | --- |
| Feature math | `FeatureSnapshot` stores snapshot ids and clocks. It does not store computed feature values |
| Hit probability calculator | `ProjectionDistribution` is the stat distribution and has no line or side. `MarketComparison` holds `p_win`, `p_loss`, and `p_push` for one line and side. Nothing calculates them |
| Data-confidence score | Gates are categorical. There is no numeric confidence |
| Calibration record | A stage can be labeled `uncalibrated`. No bucket or observed frequency is stored |
| Price selection | `MarketComparison` can name a synthetic line and side. Implied probability and no-vig probability are fixed null |
| Decision threshold | `Decision` separates `inputs_eligible` from a recommendation. Threshold, stake, and bankroll sizing are fixed null. `live_recommendation` cannot be true |
| Settlement grading | `SettlementRecord` can name win, loss, push, or void. Push is not void. `unit_profit` is fixed null, `graded` is false, and the pipeline does not emit one |
| Closing line | No closing snapshot distinct from the bet-time snapshot |
| Bankroll | Unresolved module. No stake, Kelly fraction, or account balance |
| Job run | No orchestration record for inputs, quota headers, or invalidation |
| `snapshot_id` on `RosterEntry`, `PlayerGameLog`, and `InjuryReport` | Identity of the observation sits on a wrapper or is absent |
| Pair id on `MarketSnapshot` | Over and under are two snapshots that share attributes, not one linked record |
| Opponent and season on `PlayerGameLog` | Joined from the schedule at read time |
| Supplemental roster status | Accepted as membership and then discarded |
| Return date and minutes limit on `InjuryReport` | Carried only by evidence objects the caller must build |
| Offensive rating, rest, and usage on `TeamContext` | Pace and defensive rating are the only metrics |

A planned decision record, when added, should point at the rules `audit_id`, the quote `snapshot_id`, the projection id, and the comparison id. It should not embed a fresh copy of the odds that can drift from the snapshot.
