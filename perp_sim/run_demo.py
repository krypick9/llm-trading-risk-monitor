"""
Quick demo: can the exchange produce a liquidation cascade?

Run:   python run_demo.py

Uses only scripted bots (no AI, no cost). Prints text only; writes the full
step-by-step record of the detailed run to output/.
"""

import os
import sys

from bots import ContrarianBot, CrowdBot, MakerBot, TrendBot, ValueBot
from engine import SCENARIOS, Exchange, SimConfig, write_csv

CROWD_LEVERAGE = [6, 10, 14, 18]          # a spread of leverage, like real fleets


def build_population(seed: int, crowd_copies: int = 1) -> list:
    bots = [CrowdBot(f"crowd{c}_{i}", seed, lev)
            for c in range(crowd_copies) for i, lev in enumerate(CROWD_LEVERAGE)]
    bots += [TrendBot(f"trend{i}", seed, leverage=lev) for i, lev in enumerate([4, 4, 6, 6])]
    bots += [ContrarianBot(f"contrarian{i}", seed) for i in range(2)]
    bots += [ValueBot(f"value{i}", seed) for i in range(2)]
    bots += [MakerBot("maker0", seed)]
    return bots


def run(scenario_name: str, seed: int, n_steps: int = 120, step_minutes: float = 1.0,
        liquidations: bool = True, crowd_copies: int = 1, **config_overrides) -> Exchange:
    cfg = SimConfig(n_steps=n_steps, step_minutes=step_minutes, seed=seed,
                    liquidations_enabled=liquidations, **config_overrides)
    exchange = Exchange(cfg, SCENARIOS[scenario_name])
    bots = build_population(seed, crowd_copies)
    for bot in bots:
        exchange.add_account(bot.account_id)
    for _ in range(n_steps):
        actions = []
        for bot in bots:
            actions += bot.act(exchange.view(bot.account_id))
        exchange.step(actions)
    return exchange


def max_drawdown(prices: list) -> float:
    peak, worst = prices[0], 0.0
    for p in prices:
        peak = max(peak, p)
        worst = max(worst, (peak - p) / peak)
    return worst


def summarize(exchange: Exchange) -> dict:
    recs = exchange.records
    prices = [exchange.cfg.initial_price] + [r.last_price for r in recs]
    events = exchange.liquidation_log
    first = events[0].step if events else None
    fall_after_first = None
    if first is not None:
        at_first = recs[first - 1].last_price
        fall_after_first = (at_first - min(r.last_price for r in recs[first - 1:])) / at_first
    return {
        "drawdown": max_drawdown(prices),
        "index_move": recs[-1].index_price / exchange.cfg.initial_price - 1,
        "liq_orders": len(events),
        "accounts_liquidated": len({e.account for e in events}),
        "liq_steps": sum(1 for r in recs if r.liq_count),
        "peak_liq_btc": max((r.liq_qty_long + r.liq_qty_short for r in recs), default=0.0),
        "first_liq_step": first,
        "fall_after_first_liq": fall_after_first,
        "dip_below_ref": min((r.last_price - r.index_price) / r.index_price for r in recs),
        "bad_debt": exchange.bad_debt,
        "max_near_liq": max(r.oi_near_liq_share for r in recs),
        "max_abs_funding": max(abs(r.last_funding_rate) for r in recs),
        "min_depth": min(r.depth_bid for r in recs),
    }


def pct(x, digits=1):
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def main():
    os.makedirs("output", exist_ok=True)
    seed, name = 1, "stress-thin-early"

    # ---- one detailed run ---------------------------------------------------
    ex = run(name, seed)
    shock = ex.index.shock_step
    print(f"=== Detailed run: {name}, seed {seed}, {ex.cfg.n_steps} steps of "
          f"{ex.cfg.step_minutes:g} min, {len(build_population(seed))} bots ===")
    print(f"Shock ({ex.scenario.shock_return:+.0%} on the reference price) hits at step {shock}.\n")
    print(f"{'step':>4} {'reference':>10} {'last':>10} {'vs ref':>8} {'bid depth':>10} "
          f"{'funding est/h':>13} {'liq orders':>10} {'liq BTC':>8} {'near-liq':>9}")
    for r in ex.records[max(0, shock - 3): shock + 12]:
        gap = (r.last_price - r.index_price) / r.index_price
        print(f"{r.step:>4} {r.index_price:>10.0f} {r.last_price:>10.0f} {pct(gap, 2):>8} "
              f"{r.depth_bid:>10.2f} {r.funding_rate_est * 100:>12.4f}% {r.liq_count:>10} "
              f"{r.liq_qty_long + r.liq_qty_short:>8.2f} {pct(r.oi_near_liq_share, 0):>9}")
    write_csv(ex.records, f"output/demo_{name}_seed{seed}.csv")

    on = summarize(ex)
    off = summarize(run(name, seed, liquidations=False))
    print(f"\nThis run: {on['liq_orders']} liquidation orders across {on['accounts_liquidated']} accounts; "
          f"biggest single step {on['peak_liq_btc']:.2f} BTC force-sold; bad debt ${on['bad_debt']:,.0f}.")
    print("THE TEST that matters: same run, same bots, same seed, with forced closing switched off:")
    print(f"  deepest dip of the exchange price below the reference price: "
          f"{pct(on['dip_below_ref'], 2)} with liquidations vs {pct(off['dip_below_ref'], 2)} without.")

    # ---- all 8 scenarios, several seeds ------------------------------------
    seeds = range(1, 9)
    print(f"\n=== All 8 scenarios, averaged over {len(seeds)} seeds ===")
    print(f"{'scenario':<20} {'liq accts':>9} {'runs w/ liq':>11} {'dip below ref':>14} "
          f"{'dip, liq off':>13} {'extra from liq':>15}")
    for scen in SCENARIOS:
        a = [summarize(run(scen, sd)) for sd in seeds]
        b = [summarize(run(scen, sd, liquidations=False)) for sd in seeds]
        n = len(a)
        dip_on = sum(r["dip_below_ref"] for r in a) / n
        dip_off = sum(r["dip_below_ref"] for r in b) / n
        print(f"{scen:<20} {sum(r['accounts_liquidated'] for r in a) / n:>9.1f} "
              f"{sum(1 for r in a if r['liq_orders']):>8}/{n} "
              f"{pct(dip_on, 2):>14} {pct(dip_off, 2):>13} {pct(dip_on - dip_off, 2):>15}")

    # ---- what decides whether a cascade forms --------------------------------
    print(f"\n=== What makes forced selling feed on itself? ({name}, averaged over {len(seeds)} seeds) ===")
    print("Two dials: how strongly the book is pulled back to the reference price each step,")
    print("and how many leveraged longs are crowded in. Dip below reference, liquidations on vs off:")
    print(f"{'pull to reference':>18} {'crowd size':>11} {'liq accts':>9} {'dip, liq on':>12} {'dip, liq off':>13}")
    for anchor in (0.5, 0.2, 0.1):
        for copies in (1, 3):
            a = [summarize(run(name, sd, bg_anchor_strength=anchor, crowd_copies=copies)) for sd in seeds]
            b = [summarize(run(name, sd, liquidations=False, bg_anchor_strength=anchor,
                               crowd_copies=copies)) for sd in seeds]
            n = len(a)
            print(f"{anchor:>18} {'x' + str(copies):>11} "
                  f"{sum(r['accounts_liquidated'] for r in a) / n:>9.1f} "
                  f"{pct(sum(r['dip_below_ref'] for r in a) / n, 2):>12} "
                  f"{pct(sum(r['dip_below_ref'] for r in b) / n, 2):>13}")
    print("\nFull step-by-step record written to output/. Nothing here is calibrated: "
          "every number is a placeholder.")


if __name__ == "__main__":
    sys.exit(main())
