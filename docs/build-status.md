# Build status

The project is in development. This note records what is complete, what is still unfinished, and the next step. Custom rules were not changed. The baseline mean does not emit a recommendation or a numeric adjustment. The repository does not create AWS resources. Bedrock is an optional explanation client and is disabled in the shipped config.

## Complete

- Canonical records for schedules, rosters, player logs, team context, injuries, market snapshots, stored projections, and analysis results.
- Versioned rules catalog and deterministic audit for points, assists, and rebounds. `rules_version` is `2026-10-29.nba-props-angles`. `model_version` is `none`. Numeric hypothesis adjustments stay disabled.
- Historical player-log and schedule-identity adapters. A joined log keeps the original log retrieval time, and `available_at` is not earlier than both the log retrieval and the schedule retrieval. Strict point-in-time mode does not use a schedule mapping retrieved after the cutoff.
- Odds adapter for one event's `player_points`, `player_assists`, and `player_rebounds` markets. `captured_at` is the book update time. `available_at` is the retrieval time. Over and under quotes are stored only when they share the player, event, stat, line, sportsbook, and source update time. Missing markets stay missing. The shipped request cap is 3 credits. Authentication and rate-limit failures are not retried. Keys are redacted from recorded errors and cache files.
- Event matching. Exactly one schedule game must share the home team, away team, and season, with a UTC tip within the configurable 900-second window. The schedule tip and the odds commence time are both kept, with `tip_delta_seconds` and method `home_away_season_within_tip_tolerance`. Neither timestamp replaces the other. Pregame eligibility stops at the earlier of the two tips. Reversed home and away, more than one candidate, a different season, and a gap wider than 900 seconds are quarantined.
- Example configuration, the current 30-team abbreviation map, and the current 30-team odds name map. `.env.example` keeps `THE_ODDS_API_KEY` and the other secret fields blank.
- Roster adapter for one team and one season through `commonteamroster`. Player and team ids come from the source row. `available_at` is the retrieval time. The cached Houston and Oklahoma City rosters, retrieved `2026-11-01T00:39:38.453334Z`, normalize to 34 accepted rows and 0 quarantined. That retrieval remains after the Thunder at Rockets odds response, so those quotes stay non-actionable. The explicit odds name map is unchanged.
- Explicit Odds API player map for the Thunder at Rockets slate. Each row keeps the provider spelling, the roster spelling, any reviewed alias, the canonical player and team ids, the season, and the roster retrieval time. Matching is exact after case and whitespace normalization, or an explicit alias. A name with no mapping, two players, a team outside the event, or evidence later than the odds retrieval is quarantined. Odds `captured_at`, `available_at`, and snapshot ids are not rewritten. Later identity evidence does not bypass the earlier-tip gate.

## Test results

`python -m pytest` — 162 passed in 6.88s (Python 3.12.10). No warnings. The Bedrock tests mock the client. Pytest pins `NBA_BEDROCK_FILE` to the shipped example, so this run did not call the network.

Automated tests use synthetic fixtures and do not call the network. No API key is required. The matching tests cover 600-second and 660-second gaps, the 901-second boundary, two candidates, reversed home and away, a different season, and post-tip rejection at the earlier tip.

## Live check

The prior events list was retrieved at `2026-10-31T23:32:27Z`. It was checked at `2026-11-01T00:12:05Z` and was not fresh, so one new events request was made. Documented cost 0. Headers: `x-requests-remaining` 500, `x-requests-used` 0, `x-requests-last` 0. Eleven events were returned. The schedule directory was the cached `2026-27` regular-season read, not a new schedule request. That accepted directory is not complete league coverage.

Six events were still pregame under the earlier tip. Five had already reached that earlier tip. None were ambiguous, reversed, a season mismatch, out of window, or an unknown team.

One event-odds request followed, for Oklahoma City Thunder at Houston Rockets, event `c30d3bdf126e98ced5b9389db8617926`, season `2026-27`, game `game:0022600153`. Schedule tip `2026-11-01T00:30:00Z`. Odds commence time `2026-11-01T00:40:00Z`. Difference 600 seconds. Method `home_away_season_within_tip_tolerance`. Markets were points, assists, and rebounds in region `us`. Documented maximum cost 3. No retry.

Odds headers: `x-requests-remaining` 497, `x-requests-used` 3, `x-requests-last` 3. This check used 3 credits. The key was not printed.

Result with an empty player map: 0 accepted, 329 quarantined. Reasons: 326 `unresolved_player_name`, 3 `unpaired_side`. Missing markets: none.

## Retrospective player identity

The cached event-odds payload was normalized again with no new Odds API request. The schedule directory was the cached `2026-27` regular-season read, retrieved `2026-10-31T23:32:35Z`. The odds retrieval clock stayed `2026-11-01T00:12:06Z`. The event still matched `game:0022600153`, schedule tip `2026-11-01T00:30:00Z`, odds commence `2026-11-01T00:40:00Z`, difference 600 seconds.

Identity evidence is `nba_api` 1.12.0 `CommonTeamRoster` for season `2026-27`, Houston `1610612745` and Oklahoma City `1610612760`, retrieved `2026-11-01T00:39:38.453334Z`. Each source row's `SEASON` field was `2026`. Player ids were cross-checked against `nba_api.stats.static.players` in that same installed package. Static players has no team id, so team membership comes from the roster response. The raw roster response is not in Git.

Fourteen provider names are mapped. Twelve match the roster spelling exactly. Two are explicit aliases: `Jabari Smith Jr` to roster `Jabari Smith Jr.` (`player:1631095`, Houston) and `Bogdan Bogdanovic` to roster `Bogdan Bogdanović` (`player:203992`, Houston). `Jaylin Williams` is `player:1631119` on Oklahoma City. `Jalen Williams`, `player:1631114`, is a different player and is not an alias.

Reprocess result: 0 accepted, 329 quarantined. No market was missing.

| Market | `identity_not_yet_available` | `unpaired_side` |
| --- | ---: | ---: |
| `player_points` | 152 | 3 |
| `player_assists` | 70 | 0 |
| `player_rebounds` | 104 | 0 |

The 326 paired quotes resolved to a reviewed player and team, then stayed quarantined because the roster evidence time is after the odds retrieval. It is also after the earlier tip, `2026-11-01T00:30:00Z`. Those quotes were not relabeled as current, and the pregame gate was not bypassed. The 3 unpaired sides are all `player_points`. The missing side was not invented. No name remained unresolved, ambiguous, or on the wrong team.

## Unfinished, in development

- Accepted quotes from the cached Thunder at Rockets response. Identity evidence was retrieved after that odds response and after the earlier tip, so the paired quotes stay `identity_not_yet_available`.
- Three unpaired `player_points` sides in that response. They were not stored as quotes.
- The 900-second quote age. It is an unvalidated operational default. This live response was not used to measure it.
- The 900-second tip window. It covered this 11-game list, where the odds commence time was 600 or 660 seconds after the schedule tip. It is not proof that every later slate uses the same offset.
- Historical availability. Current player-log and schedule extracts have no as-of parameter.
- Team coverage. Abbreviation and odds name maps are the current 30 teams only. Neutral-site games stay quarantined. The accepted schedule directory is not complete league coverage.
- Injury reports, live recommendations, and AWS deployment. Historical box scores, a baseline mean, an experimental integer histogram, over/under probabilities, implied probability, no-vig, probability edge, EV, and a research-only decision are calculated. The shipped decision thresholds are unset. Stakes and live recommendations are not. The roster adapter reads one team at a time. It does not replace the reviewed odds name map, and it does not make the cached Thunder at Rockets quotes actionable.
- Calibrated confidence and angle coefficients. Flagged defender-return research stays off.
- `available` means the input gates passed. It is not a bet recommendation.

## Pipeline skeleton

Implemented in this step: typed contracts for a feature snapshot, a projection distribution, a market comparison, a decision, and a settlement record. `pipeline.run_pipeline` connects validation, features, projection, rule evaluation, market comparison, decision, and a structured report. Validation reuses `rules.engine.evaluate`. `available` remains the rules state. The report recommendation is `none`, and `live_recommendation` cannot be true.

Historical box scores are now calculated on that path when the rules state is `available`. The feature version is `2026-11-01.historical-box-score`. Included games are completed games before the target whose evidence was available by the cutoff, ordered by tip time. The sample average is `total / n`. The per-minute rate is `total stat / total minutes`. Variability is the sample standard deviation with divisor `n - 1`, and it is null for fewer than two games. An empty sample stores null, not zero. The shipped window is 10 games, 30 days, and a minimum of 5. Those defaults are unvalidated. Usage, shot volume, pace, defense, and lineup stay null. Historical minutes are not expected future minutes.

A baseline mean is calculated when that feature sample is sufficient and explicit expected minutes are valid. `mean = expected_minutes * selected_rate`. The unshrunk rate is `historical_stat_total / historical_minutes`. With shrinkage enabled, the rate is `(historical_stat_total + prior_rate * prior_minutes) / (historical_minutes + prior_minutes)`. The shipped file sets shrinkage off and does not contain a prior. Expected minutes are not copied from historical average minutes. A refused projection names `missingness` and stores no mean. `minutes_uncertainty` is recorded only when supplied. `outcomes` stays empty. The baseline's `model_version` is `2026-11-01.baseline-minutes-rate`. Its calibration status stays `not_applicable`, and its coefficients stay unvalidated. The rules catalog `model_version` stays `none`.

`evaluation.score.evaluate_baseline` scores supplied pregame means against later stats. Points, assists, and rebounds are separate. Eligible error is prediction minus actual. The report gives sample count, MAE, RMSE, and signed bias, and it lists excluded and unavailable rows with reasons. It does not invent a projection.

The experimental distribution `2026-11-01.integer-residual-histogram` places training residuals around the target baseline mean on nonnegative integers, with one explicit tail bin above the cap. `location = baseline_mean + (observed_stat - predicted_mean)`. Negative locations are moved to 0. Non-integer locations split between the two surrounding integers. The distribution mean and its gap versus the baseline are reported when the tail is empty, and left null when the tail has mass. Exact mean preservation is not guaranteed. Fixed minutes with tolerance zero are an exact sample condition, recorded as `exact_minutes_sample`. They are not a distribution over minutes. Positive smoothing adds its pseudocount to every integer and to the tail, which creates unresolved tail mass, leaves the numeric mean null, and leaves CRPS and above-cap log scores unavailable. The candidate is uncalibrated.

When that histogram is supplied, the comparison stage calculates over/under win, loss, and push probabilities at the quote's line. Over wins when the stat is greater than the line, loses when it is less, and pushes when it is equal. Under reverses win and loss. The line is not rounded, so a half-point line has push probability zero. Tail mass is used only when every integer above the cap is on the same side of the line (`line < support_max + 1`). A line that splits the tail is `tail_split_by_line`, and the resolved mass is not renormalized. A zero probability on an identified outcome is kept in aggregate log loss as positive infinity. The comparison stays experimental and uncalibrated and does not itself store American odds, implied probability, or a no-vig price.

`src/edge` then prices that comparison against the quote. Positive American odds `A` use `decimal = 1 + A / 100`. Negative odds use `decimal = 1 + 100 / |A|`. Raw implied probability is `1 / decimal` and is labeled as containing vig. `EV = p_win * (decimal - 1) - p_loss`. A push contributes nothing. If `p_win + p_loss` is zero, EV is zero and probability edge is unavailable. Otherwise probability edge is `p_win / (p_win + p_loss)` minus the raw implied probability, or minus the proportional no-vig probability when the opposite quote matches the book, event, player, stat, line, and `captured_at`. The record names the benchmark. No-vig is not a true probability. Distribution mean minus the line is a separate field and is null when the mean is unresolved. The price version is `2026-11-01.american-price-edge`. `MarketComparison.implied_probability` stays null. The legacy decision stays `pass` / `not_implemented` and does not copy the edge or the EV.

`src/decision` applies `2026-11-01.research-decision`. The shipped file leaves both minima and the edge benchmark empty. A labeled synthetic test may supply minima. The result is `research_candidate`, `no_bet`, or `unavailable`, with every gate. Positive EV and positive probability edge are required in addition to those minima. A required no-vig benchmark is not replaced by the raw edge. An all-push price is `no_bet`. Missing, stale, or unresolved inputs are `unavailable`. Data quality, the price id that holds hit probability, and calibration status are separate. An uncalibrated price may be a research candidate only. A synthetic comparison stays `origin: synthetic`. `live_recommendation` stays false, and stake and bankroll stay null.

Still unfinished inside that path: a live candidate, stake sizing, bankroll sizing, and settlement grading. Without a supplied histogram, comparison and the legacy decision stay `not_implemented` and no price is attached. The research result is then `unavailable`. Injected test stages may store numbers only with origin `synthetic` and calibration `uncalibrated`. The pipeline does not emit a settlement. No adapter was changed and no provider was called.

## Offline demo

`python demo.py` runs the implemented stages on labeled synthetic data and does not read `.env` or call a provider. It fits the integer histogram from synthetic training rows whose predicted mean is the calculated baseline, then prices the synthetic over 24.5 at -110. The summary names the research decision as the final research outcome. The legacy `pass` or `abstain` record is an input-eligibility note, not a second decision.

The three results are `research_candidate` under labeled synthetic-test minima of 0.01, `no_bet` when the labeled EV minimum is 1.0, and `unavailable` when minutes evidence is removed. Those minima are not an operational cutoff. The JSON after `--- demo-json ---` includes the three rule audits, source references, versions, assumptions, calibration status, and every gate.

Synthetic performance is not predictive evidence. The demo does not measure accuracy or profit on real games.

Remaining gaps: real-data evaluation, minutes uncertainty, injury and team context, settlement grading, bankroll sizing, and AWS. The demo does not add a provider, a bankroll, or a new model.

Contract choices: new id prefixes are `feature:`, `comparison:`, `decision:`, `settlement:`, `report:`, and `price:`. A projection distribution is the player's stat mass for one market and has no line or side. Win, loss, and push probabilities live on the comparison. The price record binds the same player, event, stat, line, side, book, and odds snapshot. The decision threshold, the stake, and bankroll sizing stay null. A settlement may name `win`, `loss`, `push`, or `void`. Push and void are different. `unit_profit` is fixed null and `graded` stays false. The stage callables on `PipelineStages` are the extension points. The feature, baseline, residual side probabilities, and residual price defaults calculate. The decision default does not.

## Framework blueprint

The stage map and the contracts are documented from the code in this repository. Planned and unresolved work is labeled as such. These notes do not add a provider, a projection, or a deployment.

| Document | Contents |
| --- | --- |
| `docs/architecture.md` | Stage inputs, outputs, failures, and dependencies |
| `docs/data-contracts.md` | Implemented fields, provenance, and missing fields |
| `docs/decision-policy.md` | Gates, the three custom policies, and the specified edge and EV formulas |
| `docs/evaluation-plan.md` | Baseline mean scores, settlement rules, and the distribution specification |
| `docs/build-roadmap.md` | Phases and acceptance criteria |

No model accuracy, profitability, or deployment is claimed.

## Bedrock explanation

`pipeline.run_pipeline` attaches a Bedrock explanation after the research decision. `src/reasoning/bedrock.py` uses the Bedrock Runtime Converse API when `config/bedrock.local.yaml` sets `enabled: true`. The configured profile is US Anthropic Claude Haiku 4.5 in `us-west-2`. Claude 3 Haiku is end of life. A synthetic `explain_report` call on this machine returned `explained`, kept `research_result` `unavailable`, kept the synthetic and uncalibrated labels, and left `live_recommendation` false. The shipped example stays disabled. Pytest pins `NBA_BEDROCK_FILE` to that example, so the offline suite does not call AWS. A disabled or failed call keeps the Python result and stores a deterministic fallback. The model cannot change numbers, override an abstention, drop the synthetic label, or raise calibration. Provider text is untrusted data. The odds key is not put in the prompt. The example IAM policy names that one inference profile and its destination models in us-east-1, us-east-2, and us-west-2. It does not grant access by itself.

## Next step

Stop before scheduling, deployment, or live recommendations. The offline demo remains the numerical checkpoint. This machine explains finished reports through the gitignored local Bedrock file. The shipped example stays disabled. Synthetic performance is not predictive evidence. Real-data evaluation, minutes uncertainty, injury and team context, settlement grading, bankroll sizing, and AWS job orchestration remain open. Leave the cached Thunder at Rockets quotes non-actionable.
