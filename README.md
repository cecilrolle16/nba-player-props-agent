# NBA player props agent

In-development portfolio project. This is a local foundation for pregame NBA player props: points, assists, and rebounds.

It does not call live providers, calculate a projection, make a betting recommendation, or deploy to AWS.

## Goal

Build an agent that reviews an NBA slate before tip and accepts or refuses a points, assists, or rebounds quote with an explicit reason. Code will fetch and store facts. A model may later explain those facts. The model is not a source for odds, injuries, or stats.

## Current status

The contract layer is in place. `python -m pytest` last passed with 61 tests on Python 3.12.10.

What exists now:

- Typed records for schedules, rosters, player logs, team context, injuries, market snapshots, stored projections, and analysis results
- Canonical ids, source ids, UTC timestamps, `available_at`, snapshot references, and rules/model versions
- A provider interface that declares capabilities and refuses live fetches
- Validated example configuration in `config/` and `.env.example`
- A versioned rules catalog and a deterministic audit

`model_version` is `none`. `rules_version` is `2026-10-29.nba-props-angles`. Numeric hypothesis adjustments are disabled and their magnitudes are unset. Fixtures use synthetic players, teams, and books.

Input gates already reject malformed odds, unresolved players, stale injury reports, unknown provider capabilities, missing history, and missing availability or minutes evidence. Three custom angle policies are audited on every evaluation:

- Defender return applies to points only, and only with confirmed return facts.
- A streak by itself does not raise a projection or confidence for any of the three markets.
- Underperformance rebound requires three contemporaneous misses and evidence that other causes are excluded.

Skipping one of those angles does not, by itself, abstain on the whole analysis. Injury news, a minutes change, or a role change overrides the angles. The 180-minute injury window is an unvalidated operational default.

Details and open decisions are in [docs/build-status.md](docs/build-status.md). Record shapes and rule order are in [docs/architecture.md](docs/architecture.md).

## Planned architecture

These pieces are not implemented.

1. **Ingestion.** Adapters will map schedule, roster, log, injury, and odds providers into the canonical records. `ProviderAdapter.fetch` currently returns `live_integration_not_implemented`.
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

Leave `ODDS_API_KEY`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` empty. This step does not read them. Do not commit `.env`.

## Tests

From the repository root:

```bash
python -m pytest
```

The tests use synthetic JSON and YAML fixtures. They do not make network calls.

## Publishing

Commit `.env.example` only. Real environment files, key files, and provider or licensed datasets are ignored. The checked-in fixtures are synthetic and are safe to publish.
