"""
Run one simulation and (optionally) write the per-step record to CSV.

    python run.py --scenario normal --steps 600 --seed 0 --out run.csv

The loop is the whole experiment harness: build the population, open accounts,
and on every step ask each trader for actions and hand them to the exchange.
Swap scripted traders for LLM agents by passing n_llm (needs an llm_factory,
not wired up here).
"""

import argparse

from engine import Exchange, SimConfig, get_scenario
from population import PopulationSpec, add_accounts, build_population


def run(scenario_name="normal", n_steps=600, seed=0, n_llm=0, llm_factory=None,
        spec=None, cfg_overrides=None):
    scenario = get_scenario(scenario_name)
    cfg = SimConfig(n_steps=n_steps, seed=seed, **(cfg_overrides or {}))
    spec = spec or PopulationSpec()
    pop = build_population(seed, spec, n_llm=n_llm, llm_factory=llm_factory)
    ex = Exchange(cfg, scenario)
    add_accounts(ex, pop)
    for _ in range(n_steps):
        view_actions = []
        for trader in pop.traders:
            view_actions.extend(trader.act(ex.view(trader.account_id)))
        ex.step(view_actions)
    return ex, pop


def summarize(ex) -> dict:
    recs = ex.records
    liq = sum(r.liq_count for r in recs)
    return {
        "steps": len(recs),
        "liquidations": liq,
        "liq_notional": round(sum(r.liq_notional for r in recs), 2),
        "bad_debt": round(ex.bad_debt, 2),
        "final_last_price": round(ex.last_price, 2),
        "final_index_price": round(ex.index_price, 2),
        "trades": sum(r.n_trades for r in recs),
        "flat_minute_pct": round(100.0 * sum(1 for r in recs if r.price_return == 0.0) / max(1, len(recs)), 2),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="normal")
    p.add_argument("--steps", type=int, default=600)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--n_llm", type=int, default=0)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    ex, pop = run(args.scenario, args.steps, args.seed, n_llm=args.n_llm)
    print(summarize(ex))
    if args.out:
        from engine import write_csv
        write_csv(ex.records, args.out)
        print(f"wrote {args.out}")
