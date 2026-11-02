# Evaluation plan

This plan says how later results would be graded and how calibration would be measured. It does not report accuracy or profit. No settlement sample exists. The cached Thunder at Rockets quotes were quarantined and are out of every score.

## What can be checked now

Implemented tests replay the rules on synthetic subjects. They check gate precedence, the three angle policies, disabled adjustments, provenance cutoffs, and odds normalization. `audit_id` is deterministic for the same subject and catalog. Those tests do not call the network.

A passing audit means the recorded inputs satisfied the gates at `as_of`. It does not mean the side would have won.

`evaluation.score.evaluate_baseline` scores a supplied baseline mean against a later observed stat. It does not build the projection, invent expected minutes, or emit a recommendation. Points, assists, and rebounds are separate samples. For eligible pairs, error is prediction minus actual. MAE is the mean absolute error, RMSE is the square root of the mean squared error, and signed bias is the mean error. An empty sample stores null scores, not zeros. The report's `validation_status` is `unvalidated` and its calibration status is `not_applicable`. The rules catalog `model_version` stays `none`. The baseline projection's own `model_version` is `2026-11-01.baseline-minutes-rate`.

A pair is eligible only when the prediction's `model_version` is that baseline, its `available_at` is strictly before `decision_cutoff`, the outcome names the same player, event, and stat, the outcome's `available_at` is strictly after the cutoff, and the stat is a nonnegative integer from a played game. A missing mean, a missing stat, an identity mismatch, a late prediction, an outcome that was already available at the cutoff, a DNP, or a different model version is excluded or unavailable and is not entered as a zero error. The observed stat is stored only on an eligible case.

These checks show that the arithmetic is reproducible. They do not show that the baseline is accurate.

## Chronological splits

Specified, not applied by the scorer. The scorer does not choose a holdout or change a coefficient.

When a later run assigns splits, it orders games by tip time, not by retrieval time. The earliest 60 percent is train, the next 20 percent is validation, and the last 20 percent is the holdout. Those fractions are unvalidated. Walk-forward fitting for a game at time `t` may use only predictions whose decision cutoff is before `t` and only outcomes whose `available_at` is before `t`. Tuning, including any later choice among distribution candidates, uses train and validation only. The holdout is scored once after that choice is frozen. It is not used to pick a prior, a window, or a distribution family.

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

## Outcome distributions

The first candidate is implemented as an experiment. `model_version` is `2026-11-01.integer-residual-histogram`. It is uncalibrated. It is not a Poisson or a normal distribution, and it does not choose a side.

For each accepted training row, `residual = observed_stat - predicted_mean` and `location = target_baseline_mean + residual`. The training residual mean is not removed, so the histogram mean equals the baseline mean only in special cases. That equality is not guaranteed.

Each row's mass `1/n` is placed as follows. A negative location moves the whole mass to 0. No probability is stored on a negative integer. A location above `support_max` moves the whole mass to one tail bin, `stat > support_max`, which has no individual outcomes. An integer location inside the cap keeps the whole mass. Any other location splits linearly between `floor(location)` and the next integer, both inside the cap. Smoothing, when its pseudocount is positive, adds that count to every integer bin and to the tail, then renormalizes. The shipped pseudocount is 0. The shipped minimum sample is 8, the cap is 60, and minutes mode is `fixed` with tolerance 0. Those values are unvalidated.

The numeric distribution mean is `sum(stat * probability)` when the tail probability is zero, and the report stores that mean minus the baseline mean. When the tail probability is positive, both numbers are null. A single expected-minutes value only conditions which training rows are eligible. It is not turned into a minutes distribution. `pooled` is available only when the caller selects that mode, and the fit then says minutes were pooled rather than modeled.

Training rows must match the player, stat, and baseline model version. Their prediction must precede their own cutoff, that cutoff must precede the target cutoff, and the observed stat must become available after the training cutoff and before the target cutoff. Holdout rows and the target game are not fit. A sample below the configured minimum is unavailable.

`evaluation.distribution_score` reports `-log(p(stat))` when that integer probability is positive. A zero probability on an identified integer is an explicit `zero_probability_at_outcome` failure. A stat inside the unresolved tail is `tail_outcome_unresolved`. CRPS is `E|X - stat| - 0.5 E|X - X'|` and is unavailable whenever the tail probability is positive. Neither score is given a substitute value.

`aggregate_log_loss` keeps every supplied outcome in the sample. When every outcome is identified and at least one has probability zero, the aggregate log loss is positive infinity. It is not the mean of the other outcomes. When any outcome sits in the unresolved tail, the aggregate is unavailable and the finite scores are not averaged alone. A zero-probability outcome in that mixed sample remains in `zero_probability_count`.

Positive smoothing adds its pseudocount to every integer bin and to the tail, then renormalizes. A positive pseudocount therefore creates tail mass even when no training location exceeded the cap. That makes the numeric distribution mean null, makes CRPS unavailable, and makes an exact log score unavailable for any stat above the cap.

## Side probabilities

Implemented for this histogram only. `projection.side_probability` classifies a stored distribution at one explicit line and side. The comparison stores `p_win`, `p_loss`, and `p_push` for that player, event, stat, line, side, and odds snapshot. It stays experimental and uncalibrated.

Over wins when the integer stat is greater than the line, loses when it is less, and pushes when it is equal. Under reverses win and loss. The line is not rounded. A half-point line matches no integer, so its push probability is zero.

Each integer through the cap is classified on its own. Tail mass, the probability of every integer above the cap, is added to one class only when `line < support_max + 1`, because every such integer is then strictly above the line. A line at or above `support_max + 1` splits that unresolved tail. The comparison is unavailable with reason `tail_split_by_line`. Resolved probability is not renormalized, and the tail is not divided among win, loss, and push. If the three probabilities are not finite, nonnegative, and summed to one, the result is unavailable rather than adjusted.

The player, event, and stat on the distribution and the quote must match the request. A quote or distribution clock after the decision cutoff is `future_evidence`. A clock equal to the cutoff is still eligible. Fixed minutes with tolerance zero are recorded as `exact_minutes_sample`: the histogram was fit on rows with that expected-minutes value, and that value is not a distribution over minutes. Positive smoothing is recorded as `positive_smoothing_adds_unresolved_tail_mass`.

The comparison does not store American odds, implied probability, or a no-vig price. The pipeline decision stays `pass` / `not_implemented`. Edge, EV, and recommendations stay off. Hypothesis adjustments stay unset. Calibration buckets are still not applied.

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
