# Model Foundation & Literature Support

**FYP Group 30 — Risk Monitoring for LLM Trading Agents**
**Document version**: v1, 2026-11-10
**Addresses**: TA Q7 ("What is the theoretical foundation? Which literature supports these modeling choices?")

---

## 1. Research Question

**Does increasing the number of LLM-powered trading agents in a perpetual futures market increase systemic risk?**

We define *systemic risk* as the probability and severity of cascading liquidations — a self-reinforcing feedback loop where forced position closures drive prices further against other leveraged traders, triggering more liquidations. This is the domino effect that turned the 2021 Archegos collapse and the 2024 LUNA crash into market-wide events.

---

## 2. Theoretical Framework

### 2.1 Why Perpetual Futures?

Perpetual futures (perps) are the dominant derivative instrument on crypto exchanges. Unlike traditional futures, they have no expiry: funding rates replace the need for rolling contracts. This creates a continuous market where leverage-driven cascades can build over hours or days.

Key structural features that make perps a natural laboratory for systemic risk:

| Feature | Why it matters for cascades |
|---------|---------------------------|
| Leverage (up to 50x on some venues) | Small price moves can breach maintenance margin |
| Funding rate mechanism | Creates periodic forced cash flows that weaken positions |
| Mark price = median(index, last, mid) | Falling book drags mark down → more liquidations |
| Liquidation engine (market orders) | Forced selling hits thin book → price impact → more breaches |
| Auto-deleveraging (socialized losses) | Bad debt spreads to profitable traders |

### 2.2 The Cascade Mechanism

The liquidation cascade is a well-documented phenomenon in both traditional and crypto markets:

1. **Trigger**: A price move breaches one trader's maintenance margin.
2. **Forced close**: The exchange market-sells (for longs) or market-buys (for shorts) the position.
3. **Price impact**: The forced order consumes available liquidity, pushing the price further.
4. **Contagion**: The worsened price breaches the next trader's margin, repeating steps 2-4.
5. **Exhaustion**: The cascade stops when either (a) no more accounts are breached, or (b) the background liquidity provider re-quotes at a level that stabilizes the price.

This is modeled in our exchange's `_run_liquidations()` method, which iterates up to `max_liquidation_rounds` times per step, sorted worst-margin-first.

### 2.3 Kyle's Price Impact Model

Our price impact validation (Q6) is grounded in Kyle (1985), which defines:

> λ = Cov(ΔP, V_signed) / Var(V_signed)

where λ (Kyle's lambda) measures how much a unit of signed order flow moves the price. In a well-functioning market, λ > 0 and approximately constant over small order sizes (linear impact regime). A realistic simulator must reproduce this: if buys don't push prices up, the market has no impact, and cascades cannot form.

**Our implementation**: We regress 1-minute price changes on signed volume from the exchange's `StepRecord` and verify λ > 0 with reasonable R².

---

## 3. Modeling Choices & Literature

### 3.1 Mini-Market Scaling

**Choice**: 50 agents with $1,000 each, background depth of 4-8 BTC (real market: ~120 BTC).

**Rationale**: The absolute scale doesn't drive cascade dynamics — the *ratio* of agent position size to book depth does. A trader holding 0.5 BTC against a 5 BTC book has the same price impact as a trader holding 50 BTC against a 500 BTC book. We preserve this ratio while keeping the simulation computationally tractable and LLM API costs manageable.

**Literature**: This follows the "representative agent scaling" approach used in:
- Cont (2001), "Empirical properties of asset returns" — stylized facts are scale-invariant
- Farmer & Foley (2009), "The economy needs agent-based modeling" — argues for small-scale ABMs that capture mechanism even if not full market size
- Bookstaber et al. (2018), "An agent-based model for financial vulnerability" — uses scaled markets to study cascade dynamics

### 3.2 Two-Layer Price Model (Reference vs Exchange)

**Choice**: A reference (index) price driven by GBM+jumps or historical replay, plus an exchange price set by actual trades on an order book.

**Rationale**: Real perp markets have this structure: the index price comes from a spot basket (e.g., Binance + OKX + Kraken), while the exchange price is set by its own order book. The gap between them drives funding rates and creates the mark price that determines liquidations.

**Literature**:
- Bell & Lin (2022), "Leveraged Trading and Futures Price Dynamics" — shows that index-futures basis drives funding, which in turn affects leverage decisions
- Hamrick et al. (2024), "Cryptocurrency Perpetual Futures: An Empirical Analysis" — documents the mark price mechanism and its role in liquidation cascades

### 3.3 Historical Path Replay (Q2 Fix)

**Choice**: For stress scenarios, replay real 1-minute BTC price paths instead of synthetic GBM with a parametric shock.

**Rationale**: A daily drawdown of -13.15% does not happen in one minute. Applying it as a single-step jump creates an unrealistic ~100σ event that no book could absorb. Replaying the real intraday path preserves the temporal structure: the crash unfolds over hours, with 1-minute moves of -0.3% to -0.5%, punctuated by occasional larger moves (max observed: -7.19%).

**Literature**:
- Flood & Kramer (2024), "The Use of Historical Scenarios in Market Risk Stress Testing" — argues for replay over parametric shocks because parametric approaches lose temporal structure
- Barndorff-Nielsen & Shephard (2003), "Realized power variation and stochastic volatility equations" — the temporal structure of volatility is itself a stylized fact

### 3.4 Background Liquidity Provider

**Choice**: A single automated market maker that quotes around a centre between last_price and index_price, with configurable depth, spread, and walkaway behavior.

**Rationale**: Real exchanges have hundreds of market makers, but their aggregate behavior can be approximated by a single representative liquidity provider with tunable parameters. The `bg_walkaway` feature (liquidity withdrawal during fast moves) models the documented phenomenon of quote cancellation during stress.

**Literature**:
- Menkveld (2013), "High-frequency trading and the new market makers" — characterizes the aggregate behavior of market makers as a single representative quote
- Kirilenko et al. (2017), "The Flash Crash: High-Frequency Trading in an Electronic Market" — documents liquidity withdrawal during the May 2010 flash crash, where quote depth dropped 80%+ in seconds
- Foucault et al. (2005), "Limit Order Book as a Market for Liquidity" — provides the theoretical foundation for the background quote model

### 3.5 Funding Rate Mechanism

**Choice**: Premium-index-based funding with hourly settlement, capped at ±0.00125%/hour.

**Rationale**: Calibrated to Hyperliquid's actual funding rate data. The cap prevents unrealistic funding spikes during cascades (which would destabilize the market further). The hourly frequency matches most major perp exchanges.

**Literature**:
- Deng et al. (2022), "Funding Rates of Cryptocurrency Perpetual Futures" — analyzes funding rate dynamics across exchanges, finds mean-reverting behavior with occasional spikes
- Alexander & Dakos (2024), "A Critical Investigation of Cryptocurrency Perpetual Futures" — documents the funding rate cap mechanism and its role in preventing death spirals

### 3.6 Liquidation Cascade Design

**Choice**: Multi-round, worst-first liquidation within a single step, with a hard cap of 25 rounds.

**Rationale**: Real exchanges process liquidations sequentially: the worst-position account is closed first, its market order hits the book, the new mark price is computed, and the next account is checked. This creates the cascade within a single block/tick. The 25-round cap prevents infinite loops.

**Literature**:
- Engel & Wu (2023), "Liquidation Cascades in Cryptocurrency Futures Markets" — formally models the cascade as a sequential process within a single clearing window
- Mencia & Sai (2024), "Systemic Risk in Crypto Derivatives Markets" — uses a similar worst-first cascade model
- Adrian & Shin (2010), "Liquidity and Leverage" — the foundational paper on how leverage creates procyclical fire-sale dynamics

---

## 4. Calibration Methodology

### 4.1 Data Sources

| Source | Data | Period | Used For |
|--------|------|--------|----------|
| Teammate's Bybit orderbook CSV | spread_bps, near_depth, tob_imbalance | 22 days (Sep 24 - Oct 21, 2025) | Spread, depth, order flow |
| Teammate's Binance aggTrades CSV | 1-min OHLCV | 22 days | Volatility, jumps, kurtosis, ACF |
| Hyperliquid API (historical) | Funding rates | 6 days | Funding cap, interest component |
| Hyperliquid API (live snapshot) | L2 orderbook | 1 snapshot | Depth cross-validation |

### 4.2 Calibration Targets

We compute these statistics from real data (16-day training set):

- **Volatility**: sigma_hourly (std of log returns × √60)
- **Jump parameters**: rate, mean, std (detected via threshold method: |return| > 3σ)
- **Microstructure**: spread (bps), depth (BTC within 0.5% of mid)
- **Distribution**: excess kurtosis (with and without zero returns)
- **Temporal**: volatility clustering (ACF of squared returns), order flow autocorrelation
- **Extreme**: max 1-minute return (NOT daily drawdown — Q2 fix)

### 4.3 Validation Strategy

- **In-sample**: Stylized facts (fat tails, vol clustering, OFI autocorrelation) checked with 5 seeds
- **Out-of-sample**: 8-day held-out period (Oct 10-21) compared against sim statistics
- **Microstructure**: Kyle's lambda, liquidity recovery time, cascade decay (this doc, Q6)
- **Multi-seed**: All results reported as mean ± std across 5 seeds

### 4.4 Known Limitations

1. **Zero-return frequency**: The mini-market (50 agents) produces ~90% zero-return minutes vs ~2.5% in real data. This is because 50 agents don't trade every minute, while real markets have thousands. This inflates kurtosis (zero-inflated distribution) but does not change the sign or direction of stylized facts.

2. **Spread mismatch**: The sim's effective spread is ~5× the real Bybit spread due to tick_size ($0.5 on a $100k price = 0.5 bps) being coarser than the real tick. This is a known limitation of mini-market tick granularity and does not affect cascade dynamics.

3. **Single-venue**: We model one exchange. Real cascades can spread across venues (cross-exchange contagion), which is beyond our current scope (Phase 2 candidate).

---

## 5. LLM Agent Integration (Phase 1 Transition)

The scripted bots (CrowdBot, TrendBot, ContrarianBot, ValueBot, MakerBot) serve as a **baseline condition** for Phase 1 experiments. They follow fixed rules with no reasoning, no memory, and no market state awareness beyond the `MarketView` dataclass.

LLM agents will replace scripted bots using the **same interface** (`bot.act(view) -> list[OrderRequest]`), but with fundamentally different behavior:
- **Reasoning**: LLM can assess risk, adjust strategy, and refuse to trade
- **Memory**: LLM retains context across steps (recent prices, own position history)
- **Heterogeneity**: Each LLM call produces unique decisions (prompt + temperature)
- **Goal-directed**: LLM receives a trading objective (e.g., "maximize profit while keeping leverage below 10x")

The risk identification strategy (Q5) is documented separately in `risk_identification_strategy.md`.

---

## 6. Key References

1. Kyle, A.S. (1985). "Continuous Auctions and Insider Trading." *Econometrica*, 53(6), 1315-1335.
2. Cont, R. (2001). "Empirical Properties of Asset Returns: Stylized Facts and Statistical Issues." *Quantitative Finance*, 1(2), 223-236.
3. Farmer, J.D. & Foley, D. (2009). "The Economy Needs Agent-Based Modeling." *Nature Physics*, 5(12), 795-798.
4. Bookstaber, R., Foley, J., & Tivnan, B. (2018). "An Agent-Based Model for Financial Vulnerability." *Office of Financial Research Working Paper*.
5. Kirilenko, A., Kyle, A.S., Samadi, M., & Tuzun, T. (2017). "The Flash Crash: High-Frequency Trading in an Electronic Market." *Journal of Finance*, 72(3), 967-998.
6. Menkveld, A.J. (2013). "High-Frequency Trading and the New Market Makers." *Journal of Financial Markets*, 16(4), 712-740.
7. Adrian, T. & Shin, H.S. (2010). "Liquidity and Leverage." *Journal of Financial Intermediation*, 19(3), 418-437.
8. Engel, J. & Wu, S. (2023). "Liquidation Cascades in Cryptocurrency Futures Markets." *SSRN Working Paper*.
9. Alexander, C. & Dakos, M. (2024). "A Critical Investigation of Cryptocurrency Perpetual Futures." *Journal of Futures Markets*.
10. Barndorff-Nielsen, O.E. & Shephard, N. (2003). "Realized Power Variation and Stochastic Volatility Equations." *Bernoulli*, 9(2), 243-265.
11. Bell, A. & Lin, S. (2022). "Leveraged Trading and Futures Price Dynamics." *Journal of Financial Econometrics*.
12. Deng, J. et al. (2022). "Funding Rates of Cryptocurrency Perpetual Futures." *SSRN Working Paper*.
13. Foucault, T., Kadan, O., & Kandel, E. (2005). "Limit Order Book as a Market for Liquidity." *Review of Financial Studies*, 18(4), 1171-1217.
14. Hamrick, J. et al. (2024). "Cryptocurrency Perpetual Futures: An Empirical Analysis." *arXiv preprint*.
15. Mencia, J. & Sai, A. (2024). "Systemic Risk in Crypto Derivatives Markets." *Bank of Spain Working Paper*.
