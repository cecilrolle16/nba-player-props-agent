# NBA player props agent

In-development portfolio project. This is a local foundation for pregame NBA player props: points, assists, and rebounds.

It does not calculate a projection, make a betting recommendation, or deploy to AWS. The base provider interface does not call the network. Historical player logs and schedule identity can be read through `nba_api`. Current points, assists, and rebounds prices can be read from The Odds API for one explicit event.

## Goal

Build an agent that reviews an NBA slate before tip and accepts or refuses a points, assists, or rebounds quote with an explicit reason. Code will fetch and store facts. A model may later explain those facts. The model is not a source for odds, injuries, or stats.

## Current status

The contract layer is in place, plus historical player-log, schedule-identity, roster, and event-odds adapters. A current extract cannot establish that the same payload was available at an earlier cutoff. `python -m pytest` last passed with 106 tests in 1.38s on Python 3.12.10. Those tests do not call the network.

What exists now:

- Typed records for schedules, rosters, player logs, team context, injuries, market snapshots, stored projections, and analysis results
- Canonical ids, source ids, UTC timestamps, `available_at`, snapshot references, and rules/model versions
- A provider interface that declares capabilities and refuses undeclared live fetches
- An `nba_api` player-log adapter with local caching, timeouts, bounded retries, and request pacing
- A schedule-identity adapter that maps source game and team ids, with regular season and playoffs kept separate
- A roster adapter for one team and one season. Player and team ids come from the source row, and `available_at` is the retrieval time
- Separate log and schedule retrieval times and snapshot ids. A joined record is not marked available before both sources were retrieved
- An Odds API adapter for one event's `player_points`, `player_assists`, and `player_rebounds` markets. Source update time and retrieval time stay separate. The 900-second quote age is an unvalidated default
- Validated example configuration in `config/` and `.env.example`
- A versioned rules catalog and a deterministic audit

`model_version` is `none`. `rules_version` is `2026-10-29.nba-props-angles`. Numeric hypothesis adjustments are disabled and their magnitudes are unset. Fixtures use synthetic players, teams, and books.

Current ingestion limits: the abbreviation and team-name maps are the current 30 teams, player identity is an explicit map with reviewed aliases and a recorded evidence time, neutral-site games are quarantined, and an odds event is accepted only when the home team, away team, season, and a UTC tip within 15 minutes match exactly one schedule game. The accepted schedule directory is not complete league coverage. The odds quote-age limit is unvalidated. Downloaded provider data stays in `data/`, which is gitignored.

Input gates already reject malformed odds, unresolved players, stale injury reports, unknown provider capabilities, missing history, and missing availability or minutes evidence. Three custom angle policies are audited on every evaluation:

- Defender return applies to points only, and only with confirmed return facts.
- A streak by itself does not raise a projection or confidence for any of the three markets.
- Underperformance rebound requires three contemporaneous misses and evidence that other causes are excluded.

Skipping one of those angles does not, by itself, abstain on the whole analysis. Injury news, a minutes change, or a role change overrides the angles. The 180-minute injury window is an unvalidated operational default.

Details and open decisions are in [docs/build-status.md](docs/build-status.md). Record shapes and rule order are in [docs/architecture.md](docs/architecture.md).

## Planned architecture

These pieces are not implemented.

1. **Ingestion.** Historical player logs, schedule game identity, one-team rosters, and one-event player-prop odds can be read through the adapters. An injury adapter is not implemented. `ProviderAdapter.fetch` still returns `live_integration_not_implemented` unless the caller uses an adapter with an explicit query.
2. **Projection.** A later model may calculate a projection. Stored projection records can already be supplied as inputs. This repository does not calculate one, and it does not turn a streak or a bounce-back into a confidence boost.
3. **Reasoning.** A ChatGPT-compatible model is planned as a reasoning layer over records the code already validated.
4. **Deployment.** The scheduled job is planned to run on AWS. No infrastructure, credentials, or deploy scripts are in this repository.

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Leave `ODDS_API_KEY`, `THE_ODDS_API_KEY`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` empty in `.env.example`. The odds adapter uses `THE_ODDS_API_KEY` only when a caller supplies it. Do not commit `.env`.

## Tests

From the repository root:

```bash
python -m pytest
```

The tests use synthetic JSON and YAML fixtures. They do not make network calls.

## Publishing

Commit `.env.example` only. Real environment files, key files, and provider or licensed datasets are ignored. The checked-in fixtures are synthetic and are safe to publish.
