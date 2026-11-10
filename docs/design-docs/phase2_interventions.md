# Phase 2 Intervention Design

**FYP Group 30 — Risk Monitoring for LLM Trading Agents**
**Document version**: v1, 2026-11-10
**Addresses**: Feedback #5 (add circuit breaker and price limit to Phase 2)

---

## 1. Objective

Test whether exchange-level interventions can mitigate systemic risk caused by
LLM trading agents. Each intervention is a configurable feature in the exchange
engine that can be toggled on/off per run, enabling controlled comparison.

---

## 2. Intervention Catalog

### 2.1 Position Size Limit (already in engine)
- **Config**: `SimConfig.max_leverage` (currently 20x)
- **Phase 2 test**: Lower to 10x, 5x, 3x
- **Mechanism**: Reduces maximum position size per agent, limiting how much
  forced selling can occur during liquidation
- **Expected effect**: Fewer cascades, but also less market liquidity

### 2.2 Order Frequency Limit (to implement)
- **Config**: `SimConfig.max_orders_per_step_per_agent` (new)
- **Phase 2 test**: 1, 2, 5 orders per step per agent
- **Mechanism**: Prevents LLM agents from flooding the book with rapid-fire
  orders during panic
- **Expected effect**: Reduces short-term volatility, may delay price discovery

### 2.3 Leverage Cap Dynamic Adjustment (to implement)
- **Config**: `SimConfig.dynamic_leverage_cap` (new)
- **Phase 2 test**: When volatility > threshold, automatically lower max_leverage
- **Mechanism**: Procyclical leverage reduction, similar to CME margin hikes
- **Expected effect**: Prevents new leveraged positions from being opened
  during stress, but may trap existing positions

### 2.4 Circuit Breaker (IMPLEMENTED — Feedback #5)
- **Config**: `SimConfig.circuit_breaker_enabled`, `circuit_breaker_pct`,
  `circuit_breaker_cooldown_steps`
- **Phase 2 test**: enabled=True, pct=5%, cooldown=10 steps
- **Mechanism**: When `|last_price - index_price| / index_price > circuit_breaker_pct`,
  the exchange rejects all new MARKET orders for `cooldown_steps` steps. LIMIT
  orders are still accepted, allowing the book to rebuild.
- **Code location**: `exchange.py` → `_handle_request()` checks
  `self.circuit_breaker_active`; `step()` updates breaker state
- **Expected effect**: Directly breaks the cascade feedback loop by preventing
  forced selling from pushing price further. This is the most direct intervention.
- **Implementation status**: Code stub in place (disabled by default in Phase 1).
  Phase 2 enables it.

### 2.5 Limit Up / Limit Down (Price Band) (to implement)
- **Config**: `SimConfig.price_band_pct` (new)
- **Phase 2 test**: ±5%, ±10% from reference price
- **Mechanism**: Rejects orders that would set the last_price outside the band.
  Unlike the circuit breaker (which blocks ALL market orders), the price band
  blocks only orders that would push price further in the same direction.
- **Expected effect**: Prevents extreme price dislocation, but may create
  artificial liquidity gaps when the band is hit

### 2.6 Auto-Deleveraging (to implement)
- **Config**: `SimConfig.auto_deleverage_enabled` (new)
- **Phase 2 test**: enabled=True
- **Mechanism**: When an account is liquidated and the book cannot absorb the
  order, the exchange socializes the loss by reducing profitable traders'
  positions (pro-rata). This is how real perp exchanges handle bad debt.
- **Current state**: Bad debt is tracked (`_sweep_bad_debt`) but not socialized.
- **Expected effect**: Eliminates bad debt entirely, but transfers risk to
  profitable traders — may cause them to liquidate too (second-order cascade)

---

## 3. Phase 2 Experimental Design

### 3.1 Test Matrix

For each intervention, we run:
- **Baseline**: No intervention (Phase 1 configuration)
- **Intervention on**: With the feature enabled
- **Comparison**: Same scenarios, same seeds, same agent mix

| Intervention | Config tested | Scenarios | Seeds | Runs |
|-------------|---------------|-----------|-------|------|
| Leverage cap 10x | max_leverage=10 | 8 | 5 | 40 |
| Leverage cap 5x | max_leverage=5 | 8 | 5 | 40 |
| Circuit breaker 5% | CB enabled, pct=0.05 | 8 | 5 | 40 |
| Circuit breaker 10% | CB enabled, pct=0.10 | 8 | 5 | 40 |
| Price band ±5% | band_pct=0.05 | 8 | 5 | 40 |
| Auto-deleverage | AD enabled | 8 | 5 | 40 |
| **Total** | | | | **240** |

### 3.2 Metrics

For each intervention run, compare to baseline:
- Cascade severity reduction: Δ max_cascade_length
- Bad debt reduction: Δ bad_debt_ratio
- Price dislocation change: Δ max |mark - index| / index
- Market quality impact: Δ spread, Δ volume (interventions may harm liquidity)
- Recovery time change: Δ steps to normalize

An intervention is "effective" if it reduces cascade severity by ≥50% without
increasing spread by more than 2x or reducing volume by more than 50%.

### 3.3 Interaction Effects

Test combinations of interventions:
- Leverage cap + circuit breaker
- Circuit breaker + auto-deleverage
- All interventions combined

These interaction runs use only stress scenarios (4 of 8) with 3 seeds = 36 runs.

---

## 4. Implementation Status

| Feature | Code status | Phase |
|---------|------------|-------|
| Leverage cap | Already in config.py | Phase 1 (default) + Phase 2 (lowered) |
| Order frequency limit | Not implemented | Phase 2 |
| Dynamic leverage cap | Not implemented | Phase 2 |
| Circuit breaker | **Implemented** (disabled by default) | Phase 2 |
| Price band | Not implemented | Phase 2 |
| Auto-deleverage | Bad debt tracked, AD not implemented | Phase 2 |
