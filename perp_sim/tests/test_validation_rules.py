import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "data", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from validate_heldout import judge                                   # noqa: E402
from validate_microstructure_vs_real import impact_stats, shock_stats, band   # noqa: E402

HELD = {"sigma_hourly": 0.004, "vol_clustering_acf1_with_zeros": 0.17, "kurtosis_with_zeros": 7.6,
        "zero_return_pct": 0.56, "spread_bps_median": 0.009}


def sim(sigma=0.004, acf=0.17, kurt=7.6, zero=3.0, spread=9e-7):
    return {"sigma_hourly": (sigma, 0), "vol_acf1": (acf, 0), "return_kurtosis": (kurt, 0),
            "zero_return_pct": (zero, 0), "spread_rel": (spread, 0),
            "exchange_sigma_hourly": (sigma, 0), "exchange_vol_acf1": (acf, 0),
            "exchange_return_kurtosis": (kurt, 0), "exchange_zero_return_pct": (zero, 0),
            "exchange_worst_1min_return": (-0.01, 0), "exchange_jump_rate_per_hour": (0.1, 0)}


class HeldOutRuleTests(unittest.TestCase):
    def test_frozen_series_match_passes_three_independent_checks(self):
        rules = judge(sim(), HELD)
        self.assertTrue(all(r[3] for r in rules[:3]))
        self.assertIsNone(rules[3][3])  # flat-return frequency is descriptive only
        self.assertIsNone(rules[4][3])  # spread dial is not independent evidence

    def test_volatility_band_is_0p7_to_1p3(self):
        self.assertFalse(judge(sim(sigma=0.004 * 0.69), HELD)[0][3])
        self.assertTrue(judge(sim(sigma=0.004 * 0.71), HELD)[0][3])

    def test_tail_band_is_one_third_to_three(self):
        self.assertFalse(judge(sim(kurt=7.6 * 3.1), HELD)[2][3])
        self.assertTrue(judge(sim(kurt=7.6 * 2.9), HELD)[2][3])

    def test_flat_minutes_are_not_scored_by_the_invalid_fixed_interval(self):
        self.assertIsNone(judge(sim(zero=0.56), HELD)[3][3])


class MicrostructureHelperTests(unittest.TestCase):
    def test_impact_is_positive_and_strong_when_returns_follow_flow(self):
        rng = np.random.default_rng(0)
        flow = rng.normal(size=500)
        ret = 1e-4 * flow
        out = impact_stats(ret, flow, np.ones(500))
        self.assertGreater(out["price_impact_norm"], 0)
        self.assertGreater(out["impact_r2"], 0.99)

    def test_depth_dip_and_recovery_are_detected(self):
        ret = np.zeros(200)
        ret[:] = 1e-4 * np.sin(np.arange(200))
        ret[100] = -0.05
        depth = np.full(200, 10.0)
        depth[100:104] = 2.0
        out = shock_stats(ret, depth)
        self.assertEqual(out["n_shocks"], 1)
        self.assertAlmostEqual(out["depth_dip"], 0.2)
        self.assertEqual(out["recovery_min"], 4)

    def test_band_flags_values_outside_the_range(self):
        self.assertTrue(band("x", 1.0, 1.0, 0.5, 2.0)["within_band"])
        self.assertFalse(band("x", 0.1, 1.0, 0.5, 2.0)["within_band"])


if __name__ == "__main__":
    unittest.main()
