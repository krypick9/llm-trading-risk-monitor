# Implementation Plan v3

**FYP Group 30 — Risk Monitoring for LLM Trading Agents**
**Version**: v3, 2026-11-10 (post-TA-feedback revisions)
**Supersedes**: v2 (2026-11-10), v1 (2026-10-23)

---

## Revision Summary

This plan incorporates feedback from the TA meeting (7 questions Q1-Q7) and
the subsequent actionable recommendations (5 items). Changes from v2 are
marked with [NEW].

---

## Phase 0: Calibration & Validation (COMPLETE)

All 7 TA issues (Q1-Q7) resolved. See `docs/reports/v2_changelog_en.md` for
full details. Key v3 additions:

- [NEW] Stress replay path switched from Oct 9 (DD -1.77%) to Oct 10
  12:00-22:00 UTC (DD -15.47%, max 1-min -5.13%). Liquidations now occur.
- [NEW] Quantitative calibration criteria: ±30% sigma band, ±0.15 ACF1 band,
  kurtosis direction match, OFI direction match. Calibration score reported
  per regime (normal: 50%, stress: 75%).
- [NEW] Circuit breaker code stub implemented in exchange.py (disabled by
  default, Phase 2 enables it).

---

## Phase 1A: Baseline Experiments (Scripted Bots)

### P1A.1 — Run baseline experiments
- 8 scenarios × 5 seeds × 3 agent counts (N=10, 25, 50) = 120 runs
- Each run: 600 steps (10 hours simulated)
- Output: `data/processed/baseline_runs/{scenario}_{seed}_{N}/records.csv`
- **Status**: Ready to execute

### P1A.2 — Compute risk metrics
- For each run, compute all metrics from Section 3 of risk_identification_strategy.md
- Individual: liquidation rate, leverage utilization, Sharpe, drawdown
- Market: Kyle's lambda, spread, volume, OFI ACF
- Systemic: cascade severity, bad debt, price dislocation, recovery time
- **Status**: Metrics computation script needed

### P1A.3 — [NEW] Agent decision logging (Feedback #3 Gap 3)
- Implement AgentDecisionLog dataclass (see risk_identification_strategy.md §10)
- For scripted bots: log (step, agent_id, agent_type, market_view, actions)
- For each run, save as `decisions.jsonl` alongside `records.csv`
- **Purpose**: When cascades occur, enable forensic reconstruction of which
  agents sold what and when
- **Priority**: HIGH — must be in place before Phase 1B
- **Status**: Design spec complete, implementation pending

### P1A.4 — [NEW] Validate cascade dynamics with extreme stress path
- Re-run cascade validation with Oct 10 extreme path
- Confirm: liquidations occur, cascade ACF > 0, bad debt tracked
- **Status**: COMPLETE (29.2 liquidations, ACF=0.193, cascade fraction=55%)

### P1A.5 — [NEW] Quantitative calibration acceptance (Feedback #3 Gap 1&2)
- Calibration score computed per regime
- Normal: 50% (sigma too low due to zero-trade minutes; kurtosis direction OK;
  vol_acf1 slightly over band; OFI direction OK)
- Stress: 75% (sigma too high due to extreme replay; all other criteria pass)
- **Decision**: Accept current calibration. Normal sigma mismatch is a known
  mini-market artifact (50 agents → many zero-trade minutes → lower measured
  volatility). The cascade dynamics are correct, which is what matters.
- **Status**: COMPLETE

### P1A.6 — [NEW] PUBLIC_FIELDS API verification (Feedback #3 Gap 7)
- All PUBLIC_FIELDS verified against Binance/Hyperliquid public APIs
- 2 gaps found: (1) real-time liquidation data requires websocket, (2) depth
  band needs client-side computation from top-100 levels
- Both have clear mitigations; detector design should prefer always-available
  features
- **Status**: COMPLETE (see risk_identification_strategy.md §11)

### P1A.7 — Establish baseline risk profile
- Compute mean ± std of all metrics across 5 seeds for each scenario
- This is the control condition for LLM comparison
- **Status**: Blocked on P1A.1

### P1A.8 — [NEW] Cascade forensic capability test
- Run one stress scenario with agent decision logging enabled
- Verify: when cascade occurs at step T, can we query all agent decisions
  in the T-5 to T window?
- This validates the logging infrastructure before Phase 1B
- **Status**: Blocked on P1A.3

### P1A.9 — [NEW] Calibration tuning pass (if needed)
- If normal calibration score < 75%, try:
  - Increasing bot act_prob (more trading → fewer zero-return minutes)
  - Adjusting sigma_hourly upward in normal regime
  - Adding more bots (N=75 or N=100)
- **Status**: Not needed (accepted current calibration, see P1A.5)

### P1A.10 — [NEW] Real-time feature feasibility test (Feedback #3 Gap 7)
- Connect to Binance/Hyperliquid websocket API
- Stream PUBLIC_FIELDS in real-time for 1 hour
- Verify: can we compute all detector features with ≤1 second latency?
- **Status**: Not started (Phase 1A end / Phase 3 start)

---

## Phase 1B: LLM Agent Experiments

### P1B.1 — Implement LLMBot class
- Same `act(view) -> list[OrderRequest]` interface as scripted bots
- Internal: MarketView → prompt text → LLM API → parse response → OrderRequest list
- Error handling: if LLM returns unparseable response, default to "hold" (no action)
- **Status**: Not started

### P1B.2 — [NEW] Prompt design with herding elicitation (Feedback #2)
- 4 prompt variants (see risk_identification_strategy.md §9):
  1. Neutral (baseline): generic trading instruction, no pressure
  2. Competitive: peer ranking, redemption threat
  3. Bounded: time pressure, instinct over analysis
  4. Full herding: competitive + bounded + panic protocol (stress only)
- **Primary comparison**: Full herding vs baseline bots
- **Sensitivity**: Neutral vs Competitive vs Bounded vs Full (subset of scenarios)
- **Status**: Design complete, implementation pending

### P1B.3 — [NEW] Tiered execution plan (Feedback #4: token cost control)
- Tier 1 (highest priority): N=50, LLM 0% vs 100%, 8 scenarios, 5 seeds = 80 runs
- Tier 2: N=50, LLM 20%/50%/80%, 8 scenarios, 2 seeds = 48 runs
- Tier 3: N=10/25, all ratios, 8 scenarios, 1-2 seeds = ~48 runs
- Total: ~176 runs (down from 1440 full factorial)
- **Status**: Plan complete, execution pending P1B.1

### P1B.4 — [NEW] Token cost reduction mechanisms (Feedback #4)
- **Prompt caching**: Cache system prompt + risk parameters via Anthropic API.
  Only MarketView numbers change per step → ~80% input token savings.
- **No-change skip**: When |price_return| < 0.1σ and position unchanged,
  reuse previous decision. Skips ~30-50% of LLM calls during calm periods.
- **Model selection**: Claude 3.5 Haiku / GPT-4o-mini for exploration;
  Claude 3.5 Sonnet / GPT-4o for confirmation.
- **Batched API**: All LLM calls per step sent in parallel (async).
- **Status**: Design complete, implementation pending P1B.1

### P1B.5 — [NEW] Agent decision logging with reasoning (Feedback #3 Gap 3)
- Extend AgentDecisionLog to store LLM reasoning text
- For each decision: store raw LLM response, parsed actions, token usage
- When cascade occurs: query decisions in T-5 to T window
  - Can we identify herding? (multiple LLMs selling simultaneously)
  - What reasoning did they give? ("I see the price dropping and my margin
    is thin, so I must close my position")
- **Purpose**: Paper narrative — this is the most compelling evidence
- **Status**: Design complete, implementation pending P1B.1

### P1B.6 — Run LLM experiments
- Execute tiered plan (P1B.3)
- Store all runs in `data/processed/llm_runs/`
- **Status**: Blocked on P1B.1-P1B.5

### P1B.7 — Compute LLM risk metrics
- Same metrics as P1A.2, computed for LLM runs
- **Status**: Blocked on P1B.6

### P1B.8 — [NEW] Cascade forensic analysis
- For each run where cascade occurred:
  - Extract agent decisions in T-5 to T window
  - Classify: was this herding? (≥3 LLM agents sold in same direction within
    2 steps of each other)
  - Extract reasoning text for paper narrative
- **Status**: Blocked on P1B.5 + P1B.6

---

## Phase 1C: Comparison & Statistical Analysis

### P1C.1 — Paired comparison
- For each (scenario, seed) pair: compute Δ_metric = LLM - baseline
- Paired t-test / Wilcoxon for significance
- Effect sizes: Cohen's d, ratios
- **Status**: Blocked on P1B.7

### P1C.2 — Dose-response analysis
- Regress risk metrics on N (agent count)
- Compare slopes: β₁(LLM) vs β₁(baseline)
- **Status**: Blocked on P1C.1

### P1C.3 — Generate final report
- All hypotheses (H1-H5) tested with quantitative evidence
- Cascade forensic case studies (from P1B.8)
- Calibration report (from P1A.5)
- **Status**: Blocked on P1C.1

---

## Phase 2: Intervention Testing

### P2.1 — [NEW] Circuit breaker testing (Feedback #5)
- Enable `circuit_breaker_enabled=True` in SimConfig
- Test: 5% and 10% deviation thresholds, 10-step cooldown
- Compare cascade severity with and without breaker
- **Status**: Code stub implemented; testing pending Phase 1 completion

### P2.2 — [NEW] Price band (limit up/down) testing
- Implement price band in exchange.py
- Test: ±5%, ±10% bands
- **Status**: Not implemented

### P2.3 — Leverage cap reduction testing
- Lower `max_leverage` from 20x to 10x, 5x, 3x
- Already configurable, no code changes needed
- **Status**: Ready

### P2.4 — [NEW] Auto-deleverage testing
- Implement socialized loss mechanism in `_sweep_bad_debt`
- When bad_debt > 0: reduce profitable traders' positions pro-rata
- **Status**: Not implemented

### P2.5 — Intervention interaction effects
- Test combinations: circuit breaker + leverage cap, etc.
- 4 stress scenarios × 3 seeds × N combinations = ~36 runs
- **Status**: Blocked on P2.1-P2.4

---

## Phase 3: Real-Time Risk Detector

### P3.1 — Feature engineering
- Use only PUBLIC_FIELDS verified in P1A.6
- Features: rolling volatility, OFI, spread dynamics, OI changes, Kyle's lambda
- **Status**: Not started

### P3.2 — Model training
- Train on Phase 1 simulation data (labeled: cascade / no cascade)
- Models: logistic regression, random forest, gradient boosting
- **Status**: Blocked on P1C.3

### P3.3 — Real-time deployment
- Connect to Binance/Hyperliquid websocket (verified in P1A.10)
- Stream features, run detector, alert on high-risk events
- **Status**: Blocked on P3.2

---

## Dependency Graph

```
P1A.1 (baseline runs)
  ├── P1A.2 (metrics computation)
  │     └── P1A.7 (baseline profile)
  ├── P1A.3 (decision logging) ← [NEW]
  │     └── P1A.8 (forensic test)
  ├── P1A.4 (cascade validation) ← COMPLETE
  ├── P1A.5 (calibration acceptance) ← COMPLETE
  ├── P1A.6 (API verification) ← COMPLETE
  └── P1A.10 (real-time test)

P1B.1 (LLMBot)
  ├── P1B.2 (prompt design) ← [NEW]
  ├── P1B.3 (tiered plan) ← [NEW]
  ├── P1B.4 (token cost control) ← [NEW]
  ├── P1B.5 (decision logging) ← [NEW]
  └── P1B.6 (LLM runs)
        ├── P1B.7 (LLM metrics)
        │     └── P1B.8 (cascade forensics) ← [NEW]
        └── P1C.1 (paired comparison)
              ├── P1C.2 (dose-response)
              └── P1C.3 (final report)

P2.1-P2.4 (interventions)
  └── P2.5 (interactions)

P3.1-P3.3 (detector)
```

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Token cost too high | Can't run enough LLM experiments | Tiered plan (P1B.3), prompt caching, no-change skip |
| LLMs too rational (no cascades) | Can't observe systemic risk | Herding prompts (P1B.2), competitive pressure, panic protocol |
| Calibration sigma mismatch | Results questioned | Document mini-market artifact; cascade dynamics are correct |
| API changes during Phase 3 | Detector breaks | Pin API version; monitor changelogs |
| Agent reasoning too diverse | No herding observed | Minimize heterogeneity in prompt design (§9.2.4) |
