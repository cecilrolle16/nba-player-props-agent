# Build roadmap

Phases are bounded. A phase is done only when its acceptance checks pass offline. Later phases do not start by weakening a gate. No phase claims a profitable model or a finished deployment.

The first step after review of this blueprint is Phase 1. It does not add an API.

## Phase 0 — Blueprint

**Status:** written in this change, waiting for review.

Documents: `docs/architecture.md`, `docs/data-contracts.md`, `docs/decision-policy.md`, `docs/evaluation-plan.md`, `docs/build-roadmap.md`, and the status note in `docs/build-status.md`.

Acceptance: the documents agree with each other and with `src/`. Implemented behavior is labeled implemented. Missing fields stay missing.

## Phase 1 — Synthetic end-to-end path

**Status:** skeleton implemented. The stat distribution and the line-specific comparison are separate contracts. No probability, edge, or EV calculation.

`pipeline.run_pipeline` runs the path in `docs/architecture.md` on synthetic schedule, roster, log, quote, availability, and minutes inputs, then `evaluate`. The offline tests assert `inputs_accepted`, three angle audits, abstention when minutes are missing, and no socket. Unfinished stages return `not_implemented`. Injected values are synthetic and uncalibrated. A recommendation field stays `none`.

Acceptance:

- The composed path passes without network access.
- Existing adapter and rules tests still pass.
- No new dependency, provider, projection formula, or AWS resource.

## Phase 2 — Data-quality fixtures for ingestion

**Status:** planned.

Extend the synthetic path with the quarantine cases the adapters already implement: neutral site, unpaired side, identity after retrieval, and a schedule cutoff before the directory retrieval. Do not fetch.

Acceptance: each case names the existing reason and does not emit a snapshot or a candidate.

## Phase 3 — Missing records, still without new providers

**Status:** the four records exist on the synthetic path. The calculators that would fill them do not. Fields that stay missing are listed in `docs/data-contracts.md`.

Leave bankroll unresolved. Do not fill the records with a live feed.

Acceptance: contracts round-trip in tests, extra fields are rejected, and `evaluate` behavior is unchanged.

## Phase 4 — Features and stored projections

**Status:** historical box scores and an unshrunk-or-explicitly-shrunk baseline mean are implemented. Outcome distributions and hit probability are not.

`features/historical.py` builds one feature snapshot from accepted logs and schedule tips using only games before the target whose evidence was available at or before `as_of`. `projection/baseline.py` multiplies explicit expected minutes by the selected per-minute rate. The baseline `model_version` is `2026-11-01.baseline-minutes-rate`. The rules catalog `model_version` stays `none`. Hit probability is a separate output from `mean`. Angles still apply no magnitude.

Acceptance: a feature cannot see the same game's final stat. A current projection cannot count as a historical miss. No accuracy claim is added to the docs.

## Phase 5 — Market comparison and decisions

**Status:** planned.

Implement the no-vig rule, edge, and unit EV from `docs/decision-policy.md`. Emit `pass` or `candidate`. The edge threshold stays unresolved until an evaluation says what value was chosen and why. Unpaired quotes stay pass. Identity after the odds retrieval stays pass.

Acceptance: synthetic over/under pairs match the formula by hand. A missing side does not receive an invented price. `available` alone does not become `candidate`.

## Phase 6 — Injury and team-context adapters

**Status:** planned. Contracts already exist.

Add provider adapters only after Phases 1 through 3 have names for their outputs. Injury freshness stays at the unvalidated 180-minute default until an evaluation changes it in the catalog on purpose. Roster supplemental status is still not an injury.

Acceptance: offline fixtures cover stale, future-dated, and player-mismatch reports. A live smoke, if one is later approved, is one team or one slate and is cached outside Git.

## Phase 7 — Evaluation report

**Status:** planned.

Grade synthetic settled games with the win, loss, and push rules. Include an unscored count. Calibration buckets appear only when hit probabilities exist, and small buckets are not used to change the model.

Acceptance: the checks in `docs/evaluation-plan.md`. No ROI claim.

## Phase 8 — AWS orchestration

**Status:** planned. No account, queue, or deploy script is in this repository.

Job order for a future slate:

1. Load the last schedule cache. Refresh it only when the job asks for a new season extract. One request per season. Pace at the configured interval.
2. Refresh rosters for the two teams in each selected game before any odds request. Record the roster `retrieved_at`.
3. Refresh historical player logs for explicitly mapped players. Do not include games at or after the cutoff.
4. Refresh injuries and team context when those adapters exist.
5. Read the Odds API events list. Documented cost 0. Reuse a cache only while it is inside the freshness window.
6. For each game still before the earlier tip, and only when roster evidence is already available, request event odds for the three markets in one region. Stop if the documented maximum would exceed 3 credits or the remaining quota. Do not retry authentication or rate-limit failures. Retry only timeout and connection failures, at most twice.
7. Normalize, run rules, compare prices, and write pass or candidate.
8. Write the report and the quota headers. Do not place a bet.

Invalidation: a cache file is valid when its version, endpoint, and query keys match. A hit keeps the original `retrieved_at`. A new fetch writes a new snapshot and does not edit older snapshots. A rules or model version change invalidates decisions, not the raw provider cache. Odds for a game at or after the earlier tip are not refreshed into a current slate.

Quotas: persist `x-requests-remaining`, `x-requests-used`, and `x-requests-last` without the API key. A job that cannot prove the next call is within the cap does not call.

Monitoring: each job records start, finish, input snapshot ids, output counts by state and quarantine reason, credit headers, and the first failure reason. A failed earlier job stops the jobs that depend on it. Roster failure stops odds for that game. Odds failure does not delete the roster cache.

Retries do not move `as_of` backward and do not relabel an old quote as current.

Acceptance for this phase is a dry-run plan and an offline simulation of the order, the cap, and the stop rules. A real deploy is a separate approval.

## Unresolved items

These stay out of the phases until a later decision writes them down.

- The candidate edge threshold.
- Bankroll sizing, stake units, and any Kelly or flat-bet rule.
- Closing-line value and the definition of a closing snapshot.
- Whether no-vig should ever use a method other than the multiplicative rule already specified.
- A numeric data-confidence score distinct from the gates.
- Historical as-of access for `nba_api` extracts that do not provide it.
- Complete league coverage beyond the current 30-team maps.
- Using the 600-second and 660-second tip offsets from one 11-game list as a universal schedule rule. The implemented window is 900 seconds and is unvalidated.

## Explicitly deferred

Do not add projection math, a recommendation threshold, an injury client, or AWS resources as part of Phase 1. Do not request the Thunder at Rockets event from The Odds API again. Do not reopen its quarantined quotes.
