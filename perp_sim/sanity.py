"""
The only "validation" the minimal model does -- and on purpose.

Because the project makes RELATIVE comparisons under identical settings, the
market only has to be reasonable and the instrument only has to be able to SEE
risk changes. So instead of a calibration grid and moment-matching, we run two
cheap, honest checks:

  A. Stylized facts (qualitative): across seeds, does the baseline show
     fat tails, volatility clustering, and positive price impact? These are
     direction/sign checks, not "match real BTC to within X%".

  B. Power check (the important one): sweep leverage with ONLY scripted traders
     and confirm liquidations respond monotonically and detectably. If the
     instrument can't register a known perturbation, it can't register an agent
     effect either -- so this must pass before any Phase-1 agent experiment.

Run:  python sanity.py
"""

import statistics as stats

from run import run, summarize


def _returns(ex):
    return [r.price_return for r in ex.records if r.price_return == r.price_return]


def _kurtosis(xs):
    xs = [x for x in xs if x != 0.0]
    n = len(xs)
    if n < 4:
        return float("nan")
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / n
    if var <= 0:
        return float("nan")
    return sum((x - m) ** 4 for x in xs) / n / var ** 2  # raw kurtosis (normal ~ 3)


def _vol_acf1(xs):
    sq = [x * x for x in xs]
    n = len(sq)
    if n < 3:
        return float("nan")
    m = sum(sq) / n
    num = sum((sq[i] - m) * (sq[i - 1] - m) for i in range(1, n))
    den = sum((s - m) ** 2 for s in sq)
    return num / den if den > 0 else float("nan")


def _impact_sign(ex):
    """Crude price-impact sign: regress per-step return on signed volume.
    We only check the SLOPE IS POSITIVE (buys push up, sells push down)."""
    xs = [r.signed_volume for r in ex.records]
    ys = [r.price_return for r in ex.records]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    varx = sum((x - mx) ** 2 for x in xs)
    return cov / varx if varx > 0 else float("nan")


def stylized_facts(seeds=(0, 1, 2, 3, 4), scenario="normal", steps=600):
    ks, acfs, slopes = [], [], []
    for s in seeds:
        ex, _ = run(scenario, steps, seed=s)
        r = _returns(ex)
        ks.append(_kurtosis(r))
        acfs.append(_vol_acf1(r))
        slopes.append(_impact_sign(ex))
    k, a, sl = stats.mean(ks), stats.mean(acfs), stats.mean(slopes)
    print("A. STYLIZED FACTS (normal, %d seeds)" % len(seeds))
    print(f"   fat tails      : mean kurtosis   = {k:6.2f}   (normal = 3; >3 = fat)  -> {'PASS' if k > 3 else 'FAIL'}")
    print(f"   vol clustering : mean sq-ret ACF1 = {a:6.3f}  (>0 = clustering)       -> {'PASS' if a > 0 else 'FAIL'}")
    print(f"   price impact   : mean slope       = {sl:.3e}  (>0 = buys push up)     -> {'PASS' if sl > 0 else 'FAIL'}")
    return k > 3 and a > 0 and sl > 0


def power_check(levs=(2, 5, 10, 20), seeds=(0, 1, 2), scenario="stress", steps=600):
    from population import PopulationSpec
    print("\nB. POWER CHECK (stress, scripted only): do liquidations respond to leverage?")
    out = []
    for lev in levs:
        spec = PopulationSpec(target_leverage=float(lev), long_bias=0.7)
        total = 0
        for s in seeds:
            ex, _ = run(scenario, steps, seed=s, spec=spec)
            total += summarize(ex)["liquidations"]
        avg = total / len(seeds)
        out.append((lev, avg))
        print(f"   leverage {lev:>2}x -> {avg:7.1f} liquidations/run (avg of {len(seeds)} seeds)")
    monotone = all(out[i][1] <= out[i + 1][1] + 1e-9 for i in range(len(out) - 1))
    detectable = out[-1][1] > out[0][1]
    ok = monotone and detectable
    print(f"   monotone in leverage: {monotone} ; detectable change: {detectable} -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    a = stylized_facts()
    b = power_check()
    print("\nSUMMARY:", "stylized facts", "OK" if a else "CHECK",
          "| power check", "OK" if b else "CHECK")
