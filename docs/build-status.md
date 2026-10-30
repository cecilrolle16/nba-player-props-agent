# Build status

Step: custom angle policies aligned with the points, assists, and rebounds scope. Stopped here.

## Test results

`python -m pytest` — 61 passed in 1.83s (Python 3.12.10). No warnings.

No network calls. No provider or AWS credentials.

## What changed

Input gates and the three angle policies are separate.

Gates still abstain on the whole analysis for malformed odds, unresolved players, future timestamps, unknown or unsupported capabilities, stale injury reports, missing historical projections, and missing availability or minutes evidence. Angle audits are written even when a gate blocks.

Each angle audit records status, reason, evidence snapshot ids, `adjustment_amount: null`, and validation status. Shipped magnitudes are unset. `numeric_hypothesis_adjustments_enabled` is false. Forcing it on returns `adjustments_not_permitted` and still writes no number.

`rules_version` is `2026-10-29.nba-props-angles`. `model_version` is `none`.

Injury freshness is configurable. The shipped window is 180 minutes with validation status `unvalidated_operational_default`.

Pace, usage, and rest are future context models. They are not custom-angle coefficients.

## Intentional test changes

- Defender-return coverage no longer uses a boolean. Points qualification requires the return date, games since return, minutes since return, and key-defender evidence. Assists and rebounds stay `not_applicable`.
- The gate order no longer includes `points_only_defender_return` or `uncalibrated_adjustments_disabled`. It includes `missing_essential_evidence`.
- A requested pace adjustment is recorded as an unmodeled future context note. It does not block analysis and it does not receive a coefficient.
- The synthetic baseline injury status is `available`. A questionable, doubtful, or out report is injury news and overrides angles without changing that distinction from a stale-report skip.
- A missing injury report still does not abstain when availability and minutes evidence are present. Missing minutes or availability evidence makes the analysis `unavailable`, which is not an angle skip.

## Remaining gaps

- No projection math, live providers, or AWS deployment.
- No calibrated confidence boost and no angle coefficient.
- Flagged research for unclear defender-return context is implemented and left off.
- `available` means the input gates passed. It is not a bet recommendation.
