"""
The normal-trader baseline (the "control").

Run:   python run_baseline.py

M normal traders (population.py) trade in each of the 8 scenarios. This is the
n_llm = 0 point of the risk curve: the reference that every LLM condition will
be compared against. Later, run(..., n_llm=k, llm_factory=...) swaps k of the M
traders for LLM agents with the same cash and the same slot, leaving the market
size unchanged.

Prints text only. Every setting is still a placeholder: this shows that the
control runs and what it looks like, not that it matches the real market yet.
"""

import math
import sys

from engine import SCENARIOS, Exchange, SimConfig
from population import PopulationSpec, add_accounts, build_population
from run_demo import pct, summarize


def run(scenario_name: str, seed: int, n_llm: int = 0, spec: PopulationSpec = None,
        n_steps: int = 120, step_minutes: float = 1.0, llm_factory=None, **config_overrides):
    """One run. Returns (exchange, population). n_llm = 0 is the all-normal control."""
    cfg = SimConfig(n_steps=n_steps, step_minutes=step_minutes, seed=seed, **config_overrides)
    exchange = Exchange(cfg, SCENARIOS[scenario_name])
    population = build_population(seed, spec, n_llm, llm_factory)
    add_accounts(exchange, population)
    for _ in range(n_steps):
        actions = []
        for trader in population.bots:                 # everyone sees the same market, then everyone acts
            actions += trader.act(exchange.view(trader.account_id))
        exchange.step(actions)
    return exchange, population


def control_stats(exchange: Exchange, population) -> dict:
    """Scale-free numbers that can later be compared with real data."""
    recs = exchange.records
    returns = [r.price_return for r in recs]
    mean = sum(returns) / len(returns)
    var = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    spreads = [r.spread_rel for r in recs if r.spread_rel > 0]
    liquidated = {e.account for e in exchange.liquidation_log}
    return {
        "share_liquidated": len(liquidated) / len(population.slots),
        "hourly_vol": math.sqrt(var) * math.sqrt(60.0 / exchange.cfg.step_minutes),
        "spread_bp": 1e4 * sum(spreads) / len(spreads) if spreads else 0.0,
        "volume_per_tick": sum(r.volume for r in recs) / len(recs),
        "trades_per_tick": sum(r.n_trades for r in recs) / len(recs),
        "orders_per_tick": sum(r.n_agent_orders for r in recs) / len(recs),
    }


def main():
    seeds = range(1, 6)
    spec = PopulationSpec()
    print(f"=== Control: {spec.m_traders} normal traders, no LLM agents (n_llm = 0), "
          f"averaged over {len(seeds)} seeds ===")
    kinds = ", ".join(f"{k} {int(round(s * 100))}%" for k, s in spec.mix)
    print(f"Mix (placeholder): {kinds}\n")
    print(f"{'scenario':<20} {'% liquidated':>12} {'dip below ref':>14} {'hourly vol':>11} "
          f"{'spread bp':>10} {'BTC/tick':>9} {'trades/tick':>12}")
    for name in SCENARIOS:
        stats, dips = [], []
        for seed in seeds:
            exchange, population = run(name, seed, spec=spec)
            stats.append(control_stats(exchange, population))
            dips.append(summarize(exchange)["dip_below_ref"])
        avg = {k: sum(s[k] for s in stats) / len(stats) for k in stats[0]}
        print(f"{name:<20} {pct(avg['share_liquidated'], 1):>12} {pct(sum(dips) / len(dips), 2):>14} "
              f"{pct(avg['hourly_vol'], 2):>11} {avg['spread_bp']:>10.2f} "
              f"{avg['volume_per_tick']:>9.2f} {avg['trades_per_tick']:>12.1f}")
    print("\nNothing here is calibrated: the next step is to compare these scale-free numbers "
          "(volatility, spread, trade-size shape) with real data.")


if __name__ == "__main__":
    sys.exit(main())
