# Evaluation plan

This plan says how later results would be graded and how calibration would be measured. It does not report accuracy or profit. No settlement sample exists. The cached Thunder at Rockets quotes were quarantined and are out of every score.

## What can be checked now

Implemented tests replay the rules on synthetic subjects. They check gate precedence, the three angle policies, disabled adjustments, provenance cutoffs, and odds normalization. `audit_id` is deterministic for the same subject and catalog. Those tests do not call the network.

A passing audit means the recorded inputs satisfied the gates at `as_of`. It does not mean the side would have won.

## Settlement

Planned. One settlement row per accepted pregame snapshot after the game is final.

Inputs: the pregame `MarketSnapshot`, the decision or pass that pointed at it, the final `ScheduleGame`, and the accepted `PlayerGameLog` for that player and game. The log's `available_at` must be after tip. The snapshot's `captured_at` and `available_at` must remain the pregame values.

Output: `win`, `loss`, or `push`, using the table in `docs/decision-policy.md`, plus the stat, the line, and both snapshot ids. Profit per 1 unit stake is `profit` on a win, `-1` on a loss, and `0` on a push. That profit uses the American odds on the pregame snapshot. A later price is a different snapshot and is not the result of this bet.

Failure states:

| Condition | Result |
| --- | --- |
| Quote was quarantined or the rules state was not `available` | Not settled. Do not backfill a result |
| Game is not `final` | Not settled |
| Log is missing, DNP, or quarantined | Not settled. Do not treat a missing log as zero |
| Player or game id disagrees | `unresolved_player_id` or game mismatch. Not settled |
| More than one final log for the same player and game | Quarantine as duplicate. Not settled |

Pushes stay in the sample. Dropping pushes would bias a hit rate. Hit rate, when reported later, is wins divided by wins plus losses, and the report must also show the push count. Return is the sum of unit profits divided by the number of settled snapshots, including pushes.

Closing-line value is **unresolved** until a closing snapshot is defined. The bet-time price is the only price this plan grades.

## Calibration

Planned, and only after hit probabilities exist. Calibration compares a stated `p_win` with the later win rate among results that are neither a push nor a void.

Group settled candidates by market and by probability bucket. Buckets are widths of 0.05 from 0 to 1. A bucket reports the count, the mean stated probability, the observed win rate, and the count of pushes excluded from that rate. A bucket with fewer than 200 settled non-push results is reported and is not used to change a model. Two hundred is an operational minimum for display, not a proof of skill.

Calibration does not change data confidence and does not rewrite a pregame audit. A badly calibrated model can still have had valid inputs. A well calibrated statement on a tiny sample is not a deployment clearance.

The angle statuses are not probabilities. A `qualified` defender or rebound angle is not a bucket.

## Comparison and leakage

- Train and evaluate on facts that were available at the pregame `as_of`. A projection with `available_at` after tip is leakage.
- The same game's final stat cannot be a feature for that game's pregame projection.
- Today's projection cannot be one of the three historical misses.
- Provider extracts with no as-of parameter remain point-in-time limited. An evaluation that pretends a current log extract was knowable last season must say so, and strict point-in-time mode must fail that claim.
- Odds identity added after retrieval cannot be used to reopen that retrieval's quotes.

## What this plan will not claim

- No accuracy, ROI, or CLV figure is a result of the current repository.
- The 180-minute injury window, the 900-second quote age, and the 900-second tip window are unvalidated. An evaluation may measure them later. It may not cite them as optimized.
- A candidate threshold is unresolved, so there is no official bet list to score.
- Bankroll growth is out of scope until the unresolved sizing module exists. Unit EV is the quantity this plan can score.

## Acceptance for a future evaluation run

1. Every settled row points at one pregame snapshot and one final log.
2. Quarantined quotes are counted as unscored, with their reasons.
3. Pushes are separate from wins and losses.
4. The report shows sample size before any rate.
5. The run is repeatable from stored snapshots and does not call a provider.
