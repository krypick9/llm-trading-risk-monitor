import os, sys
_PERP_SIM = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "perp_sim"))
if _PERP_SIM not in sys.path:
    sys.path.insert(0, _PERP_SIM)
from population import PopulationSpec, add_accounts, build_population

CASH_RANGE = (500.0, 1500.0)   # average $1,000, same total capital as the old 50 x $1,000

def make_crowd(seed, n_total=50, n_llm=0, llm_factory=None):
    spec = PopulationSpec(m_traders=n_total, cash_range=CASH_RANGE)
    return build_population(seed, spec, n_llm, llm_factory)

def open_accounts(exchange, population):
    add_accounts(exchange, population)