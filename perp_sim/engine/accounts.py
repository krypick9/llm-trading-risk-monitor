"""
A trader's account: cash, position, and the maths of margin.

Position is measured in BTC: positive = long (betting up), negative = short
(betting down). Equity = cash + unrealised profit. The exchange force-closes
an account when its equity falls below the maintenance requirement.
"""

from typing import Optional

POS_EPS = 1e-9


class Account:
    def __init__(self, account_id: str, collateral: float, max_leverage: float,
                 liquidatable: bool = True, background: bool = False):
        self.id = account_id
        self.collateral = collateral        # cash deposited, adjusted by realised profit, fees, funding
        self.max_leverage = max_leverage
        self.liquidatable = liquidatable
        self.background = background        # the exchange's own liquidity provider
        self.position = 0.0
        self.entry_price = 0.0              # average price of the current position
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.funding_paid = 0.0             # positive = paid out, negative = received
        self.n_liquidations = 0

    # ---- value ------------------------------------------------------------
    def unrealized_pnl(self, mark: float) -> float:
        return self.position * (mark - self.entry_price) if self.position else 0.0

    def equity(self, mark: float) -> float:
        return self.collateral + self.unrealized_pnl(mark)

    def notional(self, mark: float) -> float:
        return abs(self.position) * mark

    def margin_buffer(self, mark: float, maintenance_rate: float) -> Optional[float]:
        """How far (as a share of position value) equity sits above the
        maintenance requirement. Negative = already liquidatable. None = flat."""
        notional = self.notional(mark)
        if notional <= 0:
            return None
        return (self.equity(mark) - maintenance_rate * notional) / notional

    def is_breached(self, mark: float, maintenance_rate: float) -> bool:
        return (self.position != 0
                and self.equity(mark) < maintenance_rate * self.notional(mark))

    def liquidation_price(self, maintenance_rate: float) -> Optional[float]:
        """The price at which this account would be force-closed (holding
        everything else fixed). None if flat or if it can never be liquidated."""
        if self.position == 0:
            return None
        q = abs(self.position)
        if self.position > 0:
            price = (q * self.entry_price - self.collateral) / (q * (1 - maintenance_rate))
            return price if price > 0 else None
        return (self.collateral + q * self.entry_price) / (q * (1 + maintenance_rate))

    # ---- trading ----------------------------------------------------------
    def apply_fill(self, side: int, qty: float, price: float, fee: float) -> None:
        signed = side * qty
        pos = self.position
        if pos == 0 or (pos > 0) == (signed > 0):
            # opening or adding: blend the entry price
            new_pos = pos + signed
            self.entry_price = (abs(pos) * self.entry_price + qty * price) / abs(new_pos)
        else:
            # reducing, closing or flipping: profit/loss is realised on the closed part
            closed = min(abs(pos), qty)
            pnl = closed * (price - self.entry_price) * (1 if pos > 0 else -1)
            self.collateral += pnl
            self.realized_pnl += pnl
            new_pos = pos + signed
            if abs(new_pos) <= POS_EPS:
                self.entry_price = 0.0
            elif (new_pos > 0) != (pos > 0):
                self.entry_price = price      # flipped side: the remainder opens at this price
        self.position = 0.0 if abs(new_pos) <= POS_EPS else new_pos
        self.collateral -= fee
        self.fees_paid += fee
