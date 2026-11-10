# LLM vs Baseline Risk Identification Strategy

**FYP Group 30 — Risk Monitoring for LLM Trading Agents**
**Document version**: v1, 2026-11-10
**Addresses**: TA Q5 ("How will you identify whether LLM agents increase systemic risk compared to baseline bots?")

---

## 1. Core Question

> If we replace N scripted bots with N LLM agents (same collateral, same leverage cap, same market), does systemic risk increase?

To answer this, we need a **controlled experiment** that isolates the effect of agent type (LLM vs scripted) while holding everything else constant.

---

## 2. Experimental Design

### 2.1 Fixed Variables (Held Constant)

These must NOT change between conditions:

| Variable | Value | Why fixed |
|----------|-------|-----------|
| Number of agents | N (e.g., 10, 25, 50) | Isolates agent-type effect from population-size effect |
| Collateral per agent | $1,000 | Equal capital base |
| Leverage cap | 20x (SimConfig default) | Same risk-taking capacity |
| Scenario | One of 8 fixed scenarios | Same market environment |
| Price path | Same seed → same index path | Eliminates price-luck confound |
| Background liquidity | Same depth/spread | Same market microstructure |
| Bot mix (baseline) | 40% Crowd, 20% Trend, 15% Contrarian, 15% Value, 10% Maker | Representative baseline |

### 2.2 Independent Variable

The **only** thing that changes between conditions:

| Condition | Agent type | Interface |
|-----------|-----------|-----------|
| Baseline | 5 scripted bot types (rule-based) | `bot.act(view) -> list[OrderRequest]` |
| LLM | N LLM agents (reasoning-based) | Same interface |
| Mixed | K scripted + (N-K) LLM | Same interface |

### 2.3 Matched-Pair Design

For each run, we use the **same seed** for both conditions. This means:
- The reference price path is identical (same `rng` seed for `IndexPriceProcess`)
- The background liquidity re-quotes identically
- The only difference is which agents are sending orders

This is a **paired comparison**: each (scenario, seed) pair gives one baseline run and one LLM run under identical market conditions. We can then use a paired t-test or Wilcoxon signed-rank test to determine if differences are significant.

### 2.4 Sample Size

| Parameter | Value |
|-----------|-------|
| Scenarios | 8 (2×2×2 factorial) |
| Seeds per scenario | 5 (42, 123, 456, 789, 2025) |
| Agent counts | N ∈ {10, 25, 50} (dose-response) |
| Conditions | Baseline, LLM, Mixed (optional) |
| Total runs | 8 × 5 × 3 × 2 = 240 runs minimum |

---

## 3. Risk Metrics (Dependent Variables)

We measure risk at three levels: **individual**, **market**, and **systemic**.

### 3.1 Individual Agent Risk

| Metric | Definition | Expected LLM difference |
|--------|-----------|----------------------|
| Liquidation rate | Fraction of agents liquidated per run | LLMs may have lower rate (risk awareness) OR higher (herding) |
| Leverage utilization | Mean leverage / max leverage | LLMs may use less (conservative) or more (overconfident) |
| Position concentration | Herfindahl index of positions | LLMs may cluster (correlated reasoning) |
| Drawdown per agent | Max equity drawdown | Measures individual risk-taking |
| Sharpe ratio | Mean return / std return | Measures risk-adjusted performance |

### 3.2 Market Quality

| Metric | Definition | Expected LLM difference |
|--------|-----------|----------------------|
| Price impact (Kyle's λ) | Cov(ΔP, signed V) / Var(V) | More correlated LLM orders → higher λ |
| Spread | Mean bid-ask spread | LLMs may widen spread (more aggressive) |
| Volume | Total volume per run | LLMs may trade more or less |
| Order flow autocorrelation | ACF of OFI at lag 1 | Correlated LLM strategies → higher ACF |
| Zero-return minutes | Fraction of steps with no trades | LLMs may reduce (more active) or increase (deliberate) |

### 3.3 Systemic Risk (Primary Outcome)

| Metric | Definition | Why it captures systemic risk |
|--------|-----------|------------------------------|
| **Cascade severity** | Max consecutive liquidation steps | Longer cascades = more systemic |
| **Cascade frequency** | Fraction of runs with ≥1 cascade event | More frequent = more systemic |
| **Bad debt** | Total losses not covered by collateral | Measures socialized loss potential |
| **Bad debt ratio** | Bad debt / total collateral | Normalized for comparison |
| **Liquidation ACF(1)** | Autocorrelation of liq counts at lag 1 | Positive ACF = cascades beget cascades |
| **OI near-liquidation** | Share of OI within 5% of liquidation | Measures system fragility |
| **Price dislocation** | Max |mark - index| / index | Measures breakdown of price discovery |
| **Recovery time** | Steps for spread to normalize post-shock | Slower recovery = more fragile |

### 3.4 Volume-Normalized Metrics

Because LLM agents may trade at different frequencies than scripted bots, raw counts (e.g., total liquidations) are confounded with trading volume. We normalize:

| Normalized metric | Formula |
|-------------------|---------|
| Liquidations per unit volume | total_liquidations / total_volume |
| Bad debt per unit OI | bad_debt / mean_open_interest |
| Cascade length per unit OI | max_cascade / mean_OI |
| Price impact per unit volume | kyle_lambda × total_volume / mean_price |

These allow fair comparison even if LLMs trade more or less frequently than bots.

---

## 4. Hypotheses

### H1 (Primary): LLM agents increase cascade severity
- **Metric**: max_cascade_length, cascade_frequency
- **Direction**: LLM > Baseline
- **Mechanism**: LLMs may exhibit correlated behavior (similar reasoning → similar positions → simultaneous liquidation when price moves)

### H2: LLM agents increase position concentration
- **Metric**: Herfindahl index of positions
- **Direction**: LLM > Baseline
- **Mechanism**: LLMs reading the same market state may reach similar conclusions, creating crowded positions

### H3: LLM agents increase price impact
- **Metric**: Kyle's lambda
- **Direction**: LLM > Baseline
- **Mechanism**: Correlated LLM orders (same-direction trades clustered in time) create larger price impact per unit volume

### H4 (Null, protective): LLM agents do NOT increase individual risk
- **Metric**: Per-agent liquidation rate, drawdown
- **Direction**: LLM ≤ Baseline (LLMs are at least as safe individually)
- **Mechanism**: LLMs have risk awareness and may refuse to overleverage

### H5 (Dose-response): Risk increases with N
- **Metric**: All systemic risk metrics
- **Direction**: Risk(N=50) > Risk(N=25) > Risk(N=10)
- **Mechanism**: More agents → more potential for crowding → larger cascades

---

## 5. Statistical Testing

### 5.1 Paired Comparison

For each (scenario, seed) pair, compute:
- Δ_metric = metric_LLM - metric_baseline

Then test whether Δ is significantly different from zero.

| Test | When to use |
|------|------------|
| Paired t-test | Metric is continuous and approximately normal |
| Wilcoxon signed-rank | Metric is non-normal (e.g., cascade counts) |
| Bootstrap CI | For metrics with unknown distribution |
| Fisher's exact test | Binary outcomes (cascade occurred: yes/no) |

Significance level: α = 0.05, with Bonferroni correction for multiple comparisons.

### 5.2 Effect Size

Beyond significance, report effect sizes:
- Cohen's d for continuous metrics
- Odds ratio for binary metrics (cascade occurred)
- Ratio: metric_LLM / metric_baseline

### 5.3 Dose-Response Analysis

Treat N (agent count) as the dose and systemic risk as the response:
- Linear regression: risk = β₀ + β₁ × N + ε
- Compare β₁ between LLM and baseline conditions
- If β₁(LLM) > β₁(baseline), LLMs amplify the population-size effect

---

## 6. Implementation Plan

### Phase 1a: Baseline Runs (no LLM API cost)
1. Run 8 scenarios × 5 seeds × 3 agent counts = 120 baseline runs
2. Compute all metrics from StepRecord + liquidation_log
3. Store as `data/processed/baseline_runs/`

### Phase 1b: LLM Runs
1. Implement `LLMBot` class with same `act(view)` interface
2. Prompt: MarketView data → LLM → parse OrderRequest list
3. Run same 8 × 5 × 3 = 120 LLM runs
4. Store as `data/processed/llm_runs/`

### Phase 1c: Comparison
1. Compute Δ metrics for each matched pair
2. Run statistical tests (paired t / Wilcoxon)
3. Generate comparison report with effect sizes

### Phase 2: Mixed Populations
1. Vary LLM fraction: 0%, 25%, 50%, 75%, 100%
2. Test whether risk scales linearly with LLM fraction or has a tipping point

---

## 7. Confound Controls

| Confound | Control method |
|----------|---------------|
| LLM temperature variation | Fix temperature=0.7 for all runs; test sensitivity separately |
| Prompt engineering bias | Use a generic prompt; do not optimize for any strategy |
| API latency | Batch all LLM calls per step; simulate synchronously |
| Token cost variation | Track and report token usage per run |
| Model drift | Use a single model version (e.g., GPT-4-2024) throughout |
| Order arrival order | Already shuffled by `rng_arrival` in Exchange.step() |

---

## 8. What Counts as "Increased Systemic Risk"?

We pre-register the following criteria. If ANY is met, we conclude that LLM agents increase systemic risk:

1. **Cascade severity**: LLM max_cascade_length > baseline max_cascade_length in ≥6/8 scenarios (paired comparison, p < 0.05)
2. **Bad debt**: LLM bad_debt_ratio > baseline in ≥6/8 scenarios (p < 0.05)
3. **Dose-response slope**: β₁(LLM) > β₁(baseline) for cascade_frequency vs N (p < 0.05)
4. **Price impact**: LLM Kyle's λ > baseline λ in ≥6/8 scenarios (p < 0.05)

If none are met but individual risk metrics (H4) show LLMs are individually safer, the conclusion is: **LLMs are individually rational but collectively dangerous** — a classic systemic risk result.
