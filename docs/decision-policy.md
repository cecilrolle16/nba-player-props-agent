# Decision policy

The implemented policy decides whether inputs are usable and how the three custom angles are recorded. It does not choose a side, a price, or a stake. Edge, EV, and the candidate decision are specified so they can be implemented later without changing the gates.

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

These are specified so later code does not collapse them. None of the numeric forms is implemented. The gates are the only confidence mechanism in the code, and they are categorical.

| Name | Question it answers | Implemented form |
| --- | --- | --- |
| Data confidence | Are the identity, timestamps, and required facts good enough to compute anything? | `available`, `unavailable`, or `skip` |
| Hit probability | What is the model's probability that the chosen side wins, excluding pushes? | Not implemented. `Projection.mean` is not this number |
| Calibration | Do stated hit probabilities match later observed frequencies? | Not implemented. See `docs/evaluation-plan.md` |

A streak, a qualified angle, or a fresh injury report does not change hit probability while magnitudes stay null.

## Edge and EV

Planned. Not implemented. The formulas below are the standard for a later calculator. They are not a measured edge and they are not a promise of profit.

American odds `A` convert to decimal profit per 1 unit stake, `profit`:

- If `A >= 100`, `profit = A / 100` and implied probability `q = 100 / (A + 100)`.
- If `A <= -100`, `profit = 100 / |A|` and `q = |A| / (|A| + 100)`.

Odds between -99 and 99 are already rejected by the market contract.

For one book, one line, and both sides present with the same `captured_at`, the overround is `q_over + q_under - 1`. The planned no-vig probability for a side is that side's implied probability divided by the sum of the two implied probabilities. This multiplicative method is the one later code should use. If either side is missing, overround and no-vig probability are absent. The missing side is not invented.

The stat distribution does not contain `p_win`. Those three probabilities, when a later calculator fills them, are stored on the comparison for one player, event, stat, line, side, book, and odds snapshot. Edge for that side is `p_win - q_reference`. `q_reference` is the no-vig probability when the pair exists, and the raw implied probability when a pair does not. The result must say which reference it used. Edge is not computed from the distribution mean by subtracting the line. Implied probability and the no-vig price are fixed null until that restriction is lifted.

Expected value per 1 unit stake, with `p_win + p_loss + p_push = 1`:

`EV = p_win * profit - p_loss * 1`

A push returns the stake and adds nothing. `p_win` is the hit probability of the chosen side. `p_loss` is the probability the other side wins. For a half-point line, `p_push` is 0. For an integer line, `p_push` is the probability the stat equals the line. A model that emits only `mean` cannot supply these three probabilities.

EV is comparable across books only at the same line. A better price changes `profit` and the implied probability. It does not change the model's `p_win` unless the line itself changes.

## Win, loss, push, and void

Planned grading, applied only after `GameStatus` is `final` and a player log for that game is accepted. It is not applied to a quote that was quarantined. The settlement contract can already name these outcomes. Nothing grades them, and `unit_profit` stays null until that restriction is lifted.

| Side | Win | Push | Loss | Void |
| --- | --- | --- | --- | --- |
| `over` | stat > line | stat = line | stat < line | The wager is cancelled |
| `under` | stat < line | stat = line | stat > line | The wager is cancelled |

The stat is points, assists, or rebounds from `PlayerGameLog`, matching the market. Half-point lines cannot push. Integer lines can. A void is a cancelled wager, including a game that is not played or a book that voids the bet. It is not a push and it is not a loss. Grading uses the log that was available after the game, not the pregame projection. The pregame snapshot id stays the thing being graded.

## Decision boundary

Planned output, one per player, game, market, and line:

| Output | When |
| --- | --- |
| `pass` | Any blocking gate matched, the quote is post-tip, identity was not yet available, the side is unpaired, no hit probability exists, or the edge is at or below the threshold |
| `candidate` | Rules state is `available`, the quote is pregame at the earlier tip, identity `available_at` is at or before the odds retrieval, a hit probability exists, and edge is above the threshold |

The threshold is **unresolved**. No default stake, minimum edge, or Kelly fraction is set. A candidate names the side, book, line, American odds, snapshot id, audit id, edge, EV, and which probability reference was used. It does not name a stake.

`AnalysisResult.state == available` is not a candidate. The cached Thunder at Rockets quotes are not candidates: identity evidence was retrieved after the odds response.

## Bankroll

Unresolved module. It is separate from the decision. The decision may exist without a stake. Sizing may later read a candidate and a bankroll snapshot. It may not change the edge or the rules audit. No unit, cap, or sizing formula is chosen here.

## LLM boundary

An LLM may explain a finished `AnalysisResult`, quarantine list, or candidate. It may not create odds, stats, injuries, player ids, hit probabilities, edge, or EV. If a narrative disagrees with the audit, the audit stands.
