"""Every validation script must describe the SAME simulator as calibration.

Why: before this test, run_calibration, validate_microstructure_vs_real,
validate_cascade_impact and verify_stylized_facts each built their own
SimConfig and population (different flow counts, clustering on/off, noise bots
or not), so their results could not be compared.

How: each script's run function is called with Exchange replaced by a recorder
that captures the SimConfig and stops. No simulation runs, so the test is fast.
The configs captured from the validation scripts must equal the one captured
from run_calibration.run_one.

If this test fails after someone edits run_calibration.run_one, update
data/scripts/sim_setup.py to match (or change both on purpose and say so in the
validation report). Do not silence the test.
"""
import dataclasses
import inspect
import os
import sys
import unittest
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "data", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import sim_setup                                       # noqa: E402
import run_calibration                                 # noqa: E402
import validate_heldout                                # noqa: E402
import validate_microstructure_vs_real as micro        # noqa: E402
import validate_cascade_impact as cascade              # noqa: E402
import verify_stylized_facts as stylized               # noqa: E402
from bots import build_reference_population           # noqa: E402
from engine import SCENARIOS                           # noqa: E402

SCENARIO_NAME = "normal-thick-synthetic-no-event"
SEED = 7


class _Captured(Exception):
    def __init__(self, cfg):
        super().__init__("captured")
        self.cfg = cfg


class _Recorder:
    def __init__(self, cfg, scenario):
        raise _Captured(cfg)


def _capture(call, *modules):
    """Run `call` with Exchange replaced in `modules`; return the SimConfig it builds."""
    saved = [(m, m.Exchange) for m in modules]
    for m in modules:
        m.Exchange = _Recorder
    try:
        call()
    except _Captured as e:
        return e.cfg
    finally:
        for m, original in saved:
            m.Exchange = original
    raise AssertionError("the script never built a SimConfig / Exchange")


def _calibration_cfg():
    scenario = SCENARIOS[SCENARIO_NAME]
    return _capture(lambda: run_calibration.run_one(scenario, seed=SEED, n_steps=600, n_bots=50),
                    run_calibration)


class ScriptConfigAlignmentTests(unittest.TestCase):
    def test_calibration_uses_the_documented_environment(self):
        cfg = _calibration_cfg()
        self.assertTrue(cfg.bg_flow_enabled)
        self.assertTrue(cfg.bg_backstop_only)
        self.assertTrue(cfg.index_vol_clustering)
        self.assertEqual(cfg.bg_flow_orders_per_min, sim_setup.FLOW_ORDERS_PER_MIN)

    def test_shared_setup_equals_calibration(self):
        cfg = _capture(lambda: sim_setup.build_exchange(SCENARIOS[SCENARIO_NAME], SEED, 600, 50),
                       sim_setup)
        self.assertEqual(cfg, _calibration_cfg())

    def test_microstructure_script_equals_calibration(self):
        cfg = _capture(lambda: micro.run_sim(SCENARIO_NAME, SEED), sim_setup)
        self.assertEqual(cfg, _calibration_cfg())

    def test_cascade_script_equals_calibration(self):
        cfg = _capture(lambda: cascade.run_one(SCENARIO_NAME, SEED, 600, 50), sim_setup)
        self.assertEqual(cfg, _calibration_cfg())

    def test_stylized_facts_script_equals_calibration(self):
        cfg = _capture(lambda: stylized.run_one(SCENARIO_NAME, SEED, 600, 50), sim_setup)
        self.assertEqual(cfg, _calibration_cfg())

    def test_heldout_script_reuses_calibration_run_one(self):
        self.assertIs(validate_heldout.run_one, run_calibration.run_one)

    def test_flow_override_changes_only_the_flow_count(self):
        cfg = _capture(lambda: micro.run_sim(SCENARIO_NAME, SEED, flow_orders=80.0), sim_setup)
        self.assertEqual(cfg.bg_flow_orders_per_min, 80.0)
        self.assertEqual(dataclasses.replace(cfg, bg_flow_orders_per_min=sim_setup.FLOW_ORDERS_PER_MIN),
                         _calibration_cfg())


class ScriptPopulationAlignmentTests(unittest.TestCase):
    def test_shared_population_is_the_calibration_reference_population(self):
        mine = Counter(b.kind for b in sim_setup.make_population(1))
        ref = Counter(b.kind for b in build_reference_population(1, n_total=50, include_crowd=False))
        self.assertEqual(mine, ref)
        self.assertNotIn("noise", mine)
        self.assertNotIn("crowd", mine)
        self.assertEqual(sum(mine.values()), 50)

    def test_calibration_builds_the_reference_population(self):
        self.assertIn("build_reference_population", inspect.getsource(run_calibration.run_one))

    def test_validation_scripts_use_sim_setup_not_make_crowd(self):
        for fn in (micro.run_sim, cascade.run_one, stylized.run_one):
            src = inspect.getsource(fn)
            self.assertIn("sim_setup", src, fn.__module__)
            self.assertNotIn("make_crowd", src, fn.__module__)


if __name__ == "__main__":
    unittest.main()
