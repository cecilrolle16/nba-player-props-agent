# Decision policy

The implemented policy decides whether inputs are usable and how the three custom angles are recorded. It does not choose a stake or a recommendation. A calculated price edge and expected value do not cross a threshold and do not become a candidate.

## Implemented gates

`rules/engine.py` runs every rule. The blocking match with the lowest precedence becomes the result. Angle audits are still produced.

| Precedence | Rule | Effect | Match |
| --- | --- | --- | --- |
| 10 | `unknown_provider_capability` | `unavailable` | The subject names a capability outside the known set |
| 12 | `unsupported_capability` | `unavailable` | The capability is known and the provider did not declare it |
| 15 | `not_yet_available` | `unavailable` | Quote `available_at` or `captured_at`, injury times, evidence `available_at`, or a projection time is after `as_of` |
| 20 | `malformed_odds` | `unavailable` | `quote_problems` finds a defect, including a market that disagrees with the subject |
| 30 | `unresolved_player_id` | `unavailable` | The subject or quote lacks a canonical player id, the ids disagree, the injury names another player, or both game ids are canonical and disagree. The stored reason for an id disagreement is `player_id_mismatch` or `game_id_mismatch` |
| 40 | `stale_injury_report` | `skip` | A supplied injury for this player is older than `injury_freshness.max_age_minutes` |
| 45 | `missing_essential_evidence` | `unavailable` | Availability is unknown or minutes are missing, including a missing snapshot id or expected minutes |
| 50 | `missing_historical_projections` | `unavailable` | `historical_required` is true and no historical projection matches the player, game, market, and cutoff |

If no blocking rule matches, the state is `available` and the reason is `inputs_accepted`.

A requested adjustment the catalog does not know becomes `unknown_adjustment` and turns an otherwise available result into `unavailable`. If the catalog flag `numeric_hypothesis_adjustments_enabled` were true, an available result would become `unavailable` with `adjustments_not_permitted`. The shipped catalog rejects that flag at load time, and every magnitude is null.

The 180-minute injury window is `unvalidated_operational_default`. It is not a proven safe threshold.

`skip` abstains on the whole analysis. An angle status of `skipped` does not.

## Implemented custom policies

All three policies are recorded on every evaluation, in this order: `defender_return`, `no_streak_chasing`, `underperformance_rebound`. `adjustment_amount` stays null. Shipped validation status is `disabled`. `flagged_research` uses `unvalidated` and is off unless `flagged_research_when_secondary_unclear` is true. The shipped file leaves it false.

Injury news (`out`, `doubtful`, or `questionable`, or an availability injury-news flag), a minutes change, a minutes restriction, or a role change sets every in-scope angle to `overridden` before the angle's own facts are judged. `probable` and `available` do not override.

### Defender return

Markets: points only. Assists and rebounds are `not_applicable`.

Required facts: confirmed return date, games since return, minutes since return, and whether the opponent is the key defender. Any gap yields `skipped`. A confirmed non-key opponent yields `skipped`. Normal minutes and healthy defensive performance yield `reduced` and still apply no number. Unclear minutes restriction or defensive rotation yields `skipped` while flagged research is off, and `flagged_research` if that option is turned on. Otherwise the status is `qualified` with reason `return_facts_verified_magnitude_unset`.

### No streak chasing

Markets: points, assists, and rebounds.

Independent evidence names are minutes, usage, matchup, shot quality, health, and game environment. Each mark is `independent`, `correlated`, or `absent`. A correlated indicator forces that mark to correlated. No streak yields `not_triggered`. A streak with no independent mark yields `unsupported` and reason `streak_alone_does_not_raise_projection`. Independent evidence yields `disclosed` and reason `independent_evidence_recorded_no_confidence_boost`. The policy flags `raises_projection_from_streak` and `raises_confidence_from_streak` are false.

### Underperformance rebound

Markets: points, assists, and rebounds.

A miss counts only when it is marked missed, it is not today's projection, and the projection was available at or before both that game's tip and `as_of`. The longest consecutive run must be at least 3. Substituting the current projection yields `skipped`. Fewer than three contemporaneous misses yields `skipped`. Minutes, usage, and shot volume must each be stably true. An unknown stability field is missing evidence. A false field is unstable volume. Each exclusion cause must be `excluded`. `uncertain`, `missing`, or `present` yields `skipped`. The causes are injury, illness, rest, reduced minutes, blowouts, and declining production drivers. A complete case is `qualified` with reason `rebound_facts_verified_magnitude_unset`.

## Three different numbers

These are specified so later code does not collapse them. Data confidence stays a categorical gate. Conditional hit probability, probability edge, and EV are calculated from an uncalibrated model. Calibration against later outcomes is not implemented.

| Name | Question it answers | Implemented form |
| --- | --- | --- |
| Data confidence | Are the identity, timestamps, and required facts good enough to compute anything? | `available`, `unavailable`, or `skip` |
| Hit probability | What is the model's probability that the chosen side wins, excluding pushes? | `p_win / (p_win + p_loss)` when that denominator is positive. It is unavailable when every outcome is a push |
| Calibration | Do stated hit probabilities match later observed frequencies? | Not implemented. See `docs/evaluation-plan.md` |

A streak, a qualified angle, or a fresh injury report does not change hit probability while magnitudes stay null.

## Edge and EV

Implemented on `PriceComparison` by `src/edge`. Version `2026-11-01.american-price-edge`. The record is experimental and uncalibrated. It is not a measured edge and it is not a promise of profit. `MarketComparison.implied_probability` and `no_vig_probability` stay null. The decision does not copy the price edge or the EV, so a pass stays a pass.

American odds `A` convert as:

- If `A` is positive, `decimal = 1 + A / 100`.
- If `A` is negative, `decimal = 1 + 100 / |A|`.
- Raw implied probability is `1 / decimal`. It contains vig.

Odds between -99 and 99 are already rejected by the market contract. Profit per 1 unit stake is `decimal - 1`.

Expected value per 1 unit stake is:

`EV = p_win * (decimal - 1) - p_loss`

A push returns the stake and adds nothing, so it has no term. If `p_win + p_loss` is zero, EV is zero and both the conditional win probability and the probability edge are unavailable with reason `all_push`.

Conditional win probability is `p_win / (p_win + p_loss)`. Probability edge is that number minus the benchmark. The benchmark is the proportional no-vig probability when a valid opposite quote matches, and the raw implied probability otherwise. The record names which one it used. This is not the distribution mean minus the line. Mean minus the line is stored separately and is null when the distribution mean is unresolved.

Proportional no-vig probability is this side's raw implied probability divided by the sum of the two sides' raw implied probabilities. It is calculated only when the opposite quote is the other side at the same book, event, player, stat, and line, with the same `captured_at`, and both clocks are at or before the cutoff. The record stores the method `proportional` and both snapshot ids. A missing or mismatched opposite quote does not block single-quote EV. A no-vig price is not a true probability.

The quote must name the same player, event, stat, line, side, and odds snapshot as the probability comparison. A book or American price stored on the comparison must match the quote. A clock after the cutoff makes the price unavailable and stores no numbers.

EV is comparable across books only at the same line. A better price changes `decimal` and the implied probability. It does not change `p_win` unless the line itself changes.

## Win, loss, push, and void

Planned grading, applied only after `GameStatus` is `final` and a player log for that game is accepted. It is not applied to a quote that was quarantined. The settlement contract can already name these outcomes. Nothing grades them, and `unit_profit` stays null until that restriction is lifted.

| Side | Win | Push | Loss | Void |
| --- | --- | --- | --- | --- |
| `over` | stat > line | stat = line | stat < line | The wager is cancelled |
| `under` | stat < line | stat = line | stat > line | The wager is cancelled |

The stat is points, assists, or rebounds from `PlayerGameLog`, matching the market. Half-point lines cannot push. Integer lines can. A void is a cancelled wager, including a game that is not played or a book that voids the bet. It is not a push and it is not a loss. Grading uses the log that was available after the game, not the pregame projection. The pregame snapshot id stays the thing being graded.

## Decision boundary

Implemented as a research policy, version `2026-11-01.research-decision`. It is not a live recommendation. `live_recommendation` stays false. Stake and bankroll stay null. The legacy pipeline decision remains `pass` or `abstain` and does not copy edge or EV.

`MarketComparison.implied_probability` is a legacy field and stays null. The calculated raw implied probability, the optional no-vig probability, the probability edge, and the EV live only on `PriceComparison`. The research decision stores that price id. It does not store a second copy of those numbers.

The shipped file `config/research-decision.example.yaml` leaves `minimum_ev`, `minimum_probability_edge`, and `edge_benchmark` empty, with `configuration_label: unset`. Empty settings do not become a default cutoff. A synthetic test may supply numbers only with `configuration_label: synthetic_test`. Those numbers are not a profitable threshold. `thresholds_validated` stays false.

The required benchmark is explicit: `raw_implied` or `no_vig`. If the policy requires no-vig and the price record's benchmark is not no-vig, the decision is `unavailable` with `required_no_vig_unavailable`. The raw edge is not substituted.

| Result | When |
| --- | --- |
| `unavailable` | Inputs are not eligible, a custom-rule audit is missing, a numeric adjustment is present, the price is missing, stale, or not the same quote, thresholds or the benchmark are unset, or the price benchmark is not the required one |
| `no_bet` | The price is an all-push, EV is not positive, probability edge is not positive, or either number is below its configured minimum |
| `research_candidate` | Every gate above passed. The price is uncalibrated. Data quality, the price id that holds hit probability, and calibration status stay separate fields |

A research candidate may come from a synthetic comparison. That decision stays `origin: synthetic`. An uncalibrated observed price may also be a research candidate. Neither one is a live bet. The three custom-rule audits are still required, in the order `defender_return`, `no_streak_chasing`, `underperformance_rebound`, and their adjustment amounts stay null.

`AnalysisResult.state == available` is data quality. It is not by itself a research candidate. The cached Thunder at Rockets quotes are not candidates: identity evidence was retrieved after the odds response, and the shipped thresholds are unset.

## Bankroll

Unresolved module. It is separate from the decision. The decision may exist without a stake. Sizing may later read a candidate and a bankroll snapshot. It may not change the edge or the rules audit. No unit, cap, or sizing formula is chosen here.

## LLM boundary

An LLM may explain a finished `AnalysisResult`, quarantine list, or candidate. It may not create odds, stats, injuries, player ids, hit probabilities, edge, or EV. If a narrative disagrees with the audit, the audit stands.
