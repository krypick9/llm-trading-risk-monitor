# LLM vs Scripted-Policy Risk Identification Strategy

**Status:** Design specification; experiments and LLM implementation have not started.
**Canonical implementation plan:** `docs/design-docs/implementation_plan_v3.md`
**Last synchronized:** 2026-11-16

## 1. Research question

In a fixed-size, calibrated simulated perpetual market, how does replacing scripted decision-makers with LLM decision-makers change market quality, synchronization, liquidity fragility, and systemic risk?

The main Phase 1 experiment keeps 100 account slots and their role mix fixed, then varies which slots use scripted or LLM policies. It is distinct from the original proposal's population-density question (`N ∈ {5,20,50,80,100}`). If the team retains a density study, run it as a separate experiment with its own capital/depth scaling rule.

The 0% LLM condition is a **scripted-policy control**, not a sample of 100 human traders. The current scripted bots are stylized strategies, and `CrowdBot` intentionally builds crowded leveraged longs; it is not a neutral normal-trader proxy.

## 2. Role-stratified heterogeneity

Using one generic prompt for every LLM risks an artificially homogeneous population. Giving every agent a unique undocumented prompt makes the treatment uninterpretable. The primary design therefore uses a small set of versioned role templates with scripted counterparts.

1. Freeze the role roster and integer counts before confirmatory runs. Candidate roles include momentum/trend, value/mean reversion, contrarian, and market making. Decide whether the artificial CrowdBot is excluded from the neutral control or included only as a separately identified stress-mechanism role.
2. Give each role a stable prompt template and output schema. Provide account/market state as structured input data; do not mutate prompt text per agent except for declared role or account state.
3. Assign LLM replacements **within each role**. Preserve each role's share as closely as integer counts allow, record the assignment seed, and balance assignments across repetitions.
4. Hold model snapshot, provider, prompt version, decoding settings, risk constraints, output parser, timeout and fallback behavior fixed in the confirmatory comparison.
5. Test alternative role mixes, model/provider diversity, prompt variants, and agent-level temperature variation as separate sensitivity experiments.

This estimates the effect of substituting an LLM decision policy for a scripted policy in the same role slots. The collective behavior that emerges after substitution—including correlated LLM decisions—is part of the treatment effect. A change in role mix or initial capital is not.

## 3. Planned treatment conditions

| LLM share | Control assignment |
|---:|---|
| 0% | Scripted policy in every fixed role slot |
| 25% | Replace one quarter of slots within each role |
| 50% | Replace half of slots within each role |
| 75% | Replace three quarters within each role |
| 100% | Role-matched LLM policy in every slot |

At the main population size, all conditions use 100 trader accounts and the same total initial trader collateral budget. If role counts do not divide evenly, use a declared rounding rule and rotate/balance assignments across seeds. Do not conflate LLM share with total population size.

## 4. Controlled variables

For a matched scenario/path pair, keep fixed:

- account count, role slots, initial positions and role assignment protocol;
- total initial trader collateral and its per-account/role allocation;
- leverage cap, maintenance margin, order-size/exposure limits and margin treatment for resting orders;
- scenario, exogenous reference path, random seed, market rules and background liquidity parameters;
- fees, funding, tick/lot size, initial order-book state, tick duration and run length;
- information available to each policy and when it is sampled;
- LLM model/prompt/configuration in confirmatory runs.

The background liquidity schedule may respond to the endogenous exchange price and stress rule. Therefore, matching the initial parameters and exogenous path does **not** mean every subsequent background quote is identical across arms. That endogenous response is part of the simulated market mechanism and must be described accurately.

The `total_trader_collateral_budget` is the sum of trader initial margin. It is not circulating currency, total futures notional, or open interest. The background liquidity provider has modeled unlimited solvency, so the current engine does not represent a closed finite cash economy. Claims about conservation of money require a separate accounting model.

## 5. Pairing, seeds, and analysis

- Pair conditions on the same scenario and exogenous price-path seed.
- Use a separate, recorded seed for the role-stratified LLM assignment and scripted-bot random streams. Do not reuse one random stream in a way that causes order-arrival differences to masquerade as treatment effects.
- Use independent historical days or synthetic paths as environment replicates. A historical replay is one fixed event path, not a fresh independent seed; changing the seed does not create a new replay path.
- Pre-register primary endpoints, estimands, exclusions, sample sizes, confidence intervals/effect sizes, and multiple-comparison handling before confirmatory runs.
- Treat pilot and calibration runs as engineering/calibration data, not confirmatory treatment evidence.

The initial analysis should cover individual trader risk, market quality, same-direction order-flow persistence, liquidity fragility, liquidations/cascade severity, bad debt, and recovery. Normalize outcomes by exposure/volume where appropriate and state the chosen denominators.

## 6. Agent decision logging

Record per decision: run ID, step, agent ID, role, policy type, input market/account snapshot, prompt/model version (LLM only), raw response or scripted action, parsed orders, latency/token use, fallback/error status, position/collateral/leverage before action, fills and position after action. This supports mechanism analysis and allows debugging invalid or missing LLM actions.

LLM reasoning text should be treated as model output, not reliable evidence of internal causal reasoning. Use it as descriptive material and pair it with the observable action/order sequence.

## 7. Phase 3 interpretation and data leakage

Train the detector on simulator-generated labels but use only features observable from a real venue. Split by entire simulation run, seed and scenario/path—not random adjacent rows from the same run. Use time-aware feature windows and labels so the model cannot see future information.

Evaluate on held-out paths, role-assignment seeds, prompt settings and scenarios. Report lead time, calibration, false alarms, and performance by regime. Real event data is an external transfer/plausibility check; it cannot establish that LLM traders caused those events or prove deployment readiness.

## 8. What the current code supports

The market engine and scripted bots support environment mechanics testing and 50-bot calibration scripts. The repository does not yet contain the 100-slot replacement runner, an LLM agent implementation, per-agent decision logs, or confirmatory Phase 1 results. See the synchronized implementation plan for the current environment-fidelity gaps and phase gates.
