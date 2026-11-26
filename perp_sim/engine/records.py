"""
What the exchange writes down at every step.

Two groups of columns, kept apart on purpose (this split is what the Phase-3
detector relies on):

  PUBLIC_FIELDS   = things a real exchange shows everyone. The ONLY columns the
                    detector is allowed to read.
  SIM_ONLY_FIELDS = things only the simulation can know (who is near liquidation,
                    how many agents are buying). For research measures and
                    labelling, never as detector inputs.

Circuit-breaker columns from the Phase-0 record were removed with that feature.
Funding columns are back (funding was restored after review -- see README).
"""

import csv
from dataclasses import asdict, dataclass, fields


@dataclass
class StepRecord:
    # ---- public -----------------------------------------------------------
    step: int = 0
    minute: float = 0.0
    index_price: float = 0.0
    last_price: float = 0.0
    mark_price: float = 0.0
    mark_basis: float = 0.0            # (mark - index) / index
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0
    spread_rel: float = 0.0            # spread / mid
    depth_bid: float = 0.0
    depth_ask: float = 0.0
    top_bid_size: float = 0.0
    top_ask_size: float = 0.0
    volume: float = 0.0
    signed_volume: float = 0.0
    order_flow_imbalance: float = 0.0  # signed_volume / volume
    n_trades: int = 0
    open_interest: float = 0.0
    funding_rate_est: float = 0.0      # the rate that would be charged now, per hour
    last_funding_rate: float = 0.0     # the rate actually charged at the last settlement
    funding_settled: bool = False      # True on the steps where funding changed hands
    liq_count: int = 0
    liq_notional: float = 0.0
    liq_qty_long: float = 0.0          # longs force-closed (forced SELLING)
    liq_qty_short: float = 0.0         # shorts force-closed (forced BUYING)
    price_return: float = 0.0          # log change of last_price
    index_return: float = 0.0
    # ---- simulation only --------------------------------------------------
    n_agent_orders: int = 0
    n_agent_buys: int = 0
    n_agent_sells: int = 0
    n_rejected_orders: int = 0
    oi_near_liq_share: float = 0.0     # share of position value within the near-liq buffer
    min_margin_buffer: float = 0.0     # smallest buffer among open positions
    bad_debt_total: float = 0.0        # losses beyond an account's cash


_ALL = [f.name for f in fields(StepRecord)]
_SIM_START = _ALL.index("n_agent_orders")
PUBLIC_FIELDS = _ALL[:_SIM_START]
SIM_ONLY_FIELDS = _ALL[_SIM_START:]


def write_csv(records: list, path: str) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_ALL)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))
