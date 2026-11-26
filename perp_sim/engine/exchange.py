"""
The exchange: ties the order book, accounts, and liquidation together.

One call to Exchange.step(actions) is one tick of the clock. In order:

  1. the reference (index) price moves
  2. stale orders expire, and the background liquidity provider re-quotes
  3. traders' orders arrive (shuffled) and are matched, with background taker flow
  4. any account that has lost too much is force-closed  <- the liquidation engine
  5. the premium is sampled, and funding is paid if an hour has passed
  6. bad debt is swept and everything is written down (one StepRecord)

Traders never touch the exchange's internals. They call view(account_id) to see
what a real trader could see and hand back a list of actions.

Trimmed from Phase-0 (see README "What was cut"): the circuit breaker and the LP
"backstop" branch (there are no MakerBots to back up here -- the LP is the only
resting liquidity). Funding settlement and historical replay were restored after
review; see README "Restored after review".
"""

import math
import random
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .accounts import Account
from .config import Scenario, SimConfig
from .flow import FLOW_ID, BackgroundTakerFlow
from .index_price import IndexPriceProcess, ReplayIndexPrice
from .orderbook import BUY, EPS, SELL, Order, OrderBook
from .records import StepRecord

BG_ID = "__background__"


# ---- what traders send in ------------------------------------------------
@dataclass
class OrderRequest:
    account_id: str
    side: int
    qty: float
    price: Optional[float] = None      # None = market order
    tif: str = "GTC"
    reduce_only: bool = False
    ttl_steps: Optional[int] = None


@dataclass
class CancelAll:
    account_id: str


# ---- what traders see ----------------------------------------------------
@dataclass
class OwnView:
    account_id: str
    collateral: float
    equity: float
    position: float
    entry_price: float
    leverage: float
    margin_buffer: Optional[float]
    liquidation_price: Optional[float]
    open_orders: int


@dataclass
class MarketView:
    step: int
    minute: float
    index_price: float
    last_price: float
    mark_price: float
    best_bid: Optional[float]
    best_ask: Optional[float]
    mid: Optional[float]
    depth_bid: float
    depth_ask: float
    funding_rate_est: float
    last_funding_rate: float
    steps_to_funding: int
    open_interest: float
    liq_count_last_step: int
    liq_notional_last_step: float
    tick_size: float = 0.1
    price_history: list = field(default_factory=list)
    own: Optional[OwnView] = None


@dataclass
class LiquidationEvent:
    step: int
    account: str
    side_closed: str                   # "long" (forced selling) or "short" (forced buying)
    qty_requested: float
    qty_filled: float
    avg_price: float
    notional: float
    mark_before: float


class _Stats:
    def __init__(self):
        self.volume = 0.0
        self.signed = 0.0
        self.n_trades = 0
        self.n_orders = 0
        self.n_buys = 0
        self.n_sells = 0
        self.n_rejected = 0


class Exchange:
    def __init__(self, cfg: SimConfig, scenario: Scenario):
        self.cfg = cfg
        self.scenario = scenario
        # Separate random streams: the index path is identical for a given
        # regime/seed regardless of how many traders there are. A replay
        # scenario follows a stored real path instead of the synthetic process.
        index_cls = ReplayIndexPrice if getattr(scenario, "use_replay", False) else IndexPriceProcess
        self.index = index_cls(cfg, scenario, random.Random(f"{cfg.seed}|index"))
        self.rng_arrival = random.Random(f"{cfg.seed}|arrival")
        self.book = OrderBook(cfg.tick_size)
        self.accounts = {}
        self.initial_trader_collateral = 0.0
        self.step_count = 0
        self.index_price = cfg.initial_price
        self.last_price = cfg.initial_price
        self.index_history = [cfg.initial_price]
        self.price_history = [cfg.initial_price]
        self.bad_debt = 0.0
        self.premium_buffer = []
        self.last_funding_rate = 0.0
        self.last_liq_count = 0
        self.last_liq_notional = 0.0
        self.records = []
        self.order_log = []
        self.liquidation_log = []
        self._oid = 0
        self.add_account(BG_ID, collateral=1e12, max_leverage=1e9,
                         liquidatable=False, background=True)
        self.bg_flow = BackgroundTakerFlow(cfg, cfg.seed)
        self.add_account(FLOW_ID, collateral=1e9, max_leverage=1e9,
                         liquidatable=False, background=True)

    # ---- setup ------------------------------------------------------------
    def add_account(self, account_id, collateral=None, max_leverage=None,
                    liquidatable=True, background=False) -> Account:
        if account_id in self.accounts:
            raise ValueError(f"Account {account_id!r} already exists")
        starting = self.cfg.initial_collateral if collateral is None else collateral
        account = Account(
            account_id, starting,
            self.cfg.max_leverage if max_leverage is None else max_leverage,
            liquidatable=liquidatable, background=background,
        )
        self.accounts[account_id] = account
        if not background:
            self.initial_trader_collateral += starting
        return account

    def _next_oid(self) -> int:
        self._oid += 1
        return self._oid

    # ---- prices -----------------------------------------------------------
    def mark_price(self) -> float:
        """The price used to judge margin: the MIDDLE of the index price, the
        last trade, and the book mid. The median means one bad number can't move
        it, but a falling book DOES drag it down -- which is what lets forced
        selling feed on itself (the cascade mechanism)."""
        bid, ask = self.book.best_bid(), self.book.best_ask()
        mid = (bid + ask) / 2 if bid is not None and ask is not None else self.last_price
        return median([self.index_price, self.last_price, mid])

    def _funding_rate_now(self) -> float:
        """The hourly funding rate implied by how far the exchange price has sat
        above or below the index since the last settlement, capped both ways."""
        if not self.premium_buffer:
            return self.last_funding_rate
        mean_premium = sum(self.premium_buffer) / len(self.premium_buffer)
        rate = mean_premium * self.cfg.funding_scale + self.cfg.funding_interest_per_hour
        cap = self.cfg.funding_cap_per_hour
        return max(-cap, min(cap, rate))

    # ---- what a trader sees -----------------------------------------------
    def view(self, account_id: str) -> MarketView:
        cfg, mm = self.cfg, self.cfg.maintenance_margin_rate
        mark = self.mark_price()
        depth_bid, depth_ask = self.book.depth(cfg.depth_band)
        acct = self.accounts[account_id]
        equity = acct.equity(mark)
        notional = acct.notional(mark)
        own = OwnView(
            account_id=account_id,
            collateral=acct.collateral,
            equity=equity,
            position=acct.position,
            entry_price=acct.entry_price,
            leverage=(notional / equity if equity > 0 else (math.inf if notional else 0.0)),
            margin_buffer=acct.margin_buffer(mark, mm),
            liquidation_price=acct.liquidation_price(mm),
            open_orders=self.book.open_orders(account_id),
        )
        return MarketView(
            step=self.step_count,
            minute=self.step_count * cfg.step_minutes,
            index_price=self.index_price,
            last_price=self.last_price,
            mark_price=mark,
            best_bid=self.book.best_bid(),
            best_ask=self.book.best_ask(),
            mid=self.book.mid(),
            depth_bid=depth_bid,
            depth_ask=depth_ask,
            funding_rate_est=self._funding_rate_now(),
            last_funding_rate=self.last_funding_rate,
            steps_to_funding=cfg.funding_interval_steps - (self.step_count % cfg.funding_interval_steps),
            open_interest=self._open_interest(),
            liq_count_last_step=self.last_liq_count,
            liq_notional_last_step=self.last_liq_notional,
            tick_size=cfg.tick_size,
            price_history=self.price_history[-61:],
            own=own,
        )

    def _open_interest(self) -> float:
        return sum(a.position for a in self.accounts.values() if a.position > 0)

    # ---- one tick of the clock --------------------------------------------
    def step(self, actions: list) -> StepRecord:
        cfg = self.cfg
        self.step_count += 1
        t = self.step_count
        prev_last, prev_index = self.last_price, self.index_price
        stats = _Stats()

        self.index_price = self.index.next(t)
        self.index_history.append(self.index_price)
        self.book.expire(t)
        self._requote_background(t, stats)

        for action in actions:
            if isinstance(action, CancelAll):
                self.book.cancel_account(action.account_id)
        requests = [a for a in actions if isinstance(a, OrderRequest)]
        requests += self._background_flow_orders(t)
        self.rng_arrival.shuffle(requests)
        for request in requests:
            self._handle_request(request, t, stats)

        events = self._run_liquidations(t, stats) if cfg.liquidations_enabled else []
        self.last_liq_count = len(events)
        self.last_liq_notional = sum(e.notional for e in events)

        self.price_history.append(self.last_price)
        self._sample_premium()
        settled = False
        if cfg.funding_enabled and t % cfg.funding_interval_steps == 0:
            self._settle_funding()
            settled = True
        self._sweep_bad_debt()

        record = self._make_record(t, stats, events, settled, prev_last, prev_index)
        self.records.append(record)
        return record

    # ---- background taker flow --------------------------------------------
    def _background_flow_orders(self, t: int) -> list:
        depth_bid, depth_ask = self.book.depth(self.cfg.depth_band)
        pairs = self.bg_flow.orders_this_step(t, self.index_history,
                                              self.scenario.sigma_hourly,
                                              depth_bid, depth_ask)
        return [OrderRequest(FLOW_ID, side, qty, price=None) for side, qty in pairs]

    # ---- orders -----------------------------------------------------------
    def _floor_lot(self, qty: float) -> float:
        lot = self.cfg.lot_size
        return round(math.floor(qty / lot + 1e-9) * lot, 8)

    def _handle_request(self, req: OrderRequest, t: int, stats: _Stats) -> None:
        acct = self.accounts.get(req.account_id)
        if acct is None or req.qty <= 0 or req.side not in (BUY, SELL):
            return
        qty = self._floor_lot(req.qty)
        price = None if req.price is None else self.book.round_price(req.price)

        if req.reduce_only:
            pos = acct.position
            if pos == 0 or (pos > 0) == (req.side > 0):
                stats.n_rejected += 1
                return
            qty = min(qty, abs(pos)) if qty > 0 else 0.0
        elif not acct.background:
            ref = price if price is not None else self.mark_price()
            qty = self._clip_for_leverage(acct, req.side, qty, ref)

        if qty < self.cfg.lot_size - EPS and not (req.reduce_only and qty > EPS):
            stats.n_rejected += 1
            return

        tif = "IOC" if price is None else req.tif
        expires = None if req.ttl_steps is None else t + req.ttl_steps
        order = Order(self._next_oid(), acct.id, req.side, qty, price, tif,
                      req.reduce_only, t, expires)
        stats.n_orders += 1
        if req.side == BUY:
            stats.n_buys += 1
        else:
            stats.n_sells += 1
        self.order_log.append((t, acct.id, req.side, qty, price))
        self._apply_fills(self.book.submit(order), stats)

    def _clip_for_leverage(self, acct: Account, side: int, qty: float, ref_price: float) -> float:
        equity = acct.equity(self.mark_price())
        if equity <= 0:
            return 0.0
        max_abs = acct.max_leverage * equity / ref_price
        base = acct.position + side * self.book.resting_qty(acct.id, side)
        if side * base >= 0:
            room = max_abs - abs(base)
        else:
            room = abs(base) + max_abs
        return self._floor_lot(max(0.0, min(qty, room)))

    def _apply_fills(self, fills: list, stats: _Stats) -> None:
        cfg = self.cfg
        for f in fills:
            value = f.price * f.qty
            self.accounts[f.taker_account].apply_fill(f.taker_side, f.qty, f.price, cfg.taker_fee * value)
            self.accounts[f.maker_account].apply_fill(-f.taker_side, f.qty, f.price, cfg.maker_fee * value)
            self.last_price = f.price
            stats.volume += f.qty
            stats.signed += f.taker_side * f.qty
            stats.n_trades += 1

    # ---- background liquidity (the resting book) --------------------------
    def _requote_background(self, t: int, stats: _Stats) -> None:
        """The exchange's own liquidity provider -- the only resting liquidity
        in the minimal market. It posts a ladder around a centre between the last
        trade and the index price (bg_anchor_strength sets how close to the
        index). In stress it retreats when prices move beyond the threshold."""
        cfg, s = self.cfg, self.scenario
        self.book.cancel_account(BG_ID)
        factor, widen, look = 1.0, 1.0, 5
        if s.bg_walkaway and len(self.price_history) > look and len(self.index_history) > look:
            local = abs(self.price_history[-1] / self.price_history[-1 - look] - 1.0)
            external = abs(self.index_history[-1] / self.index_history[-1 - look] - 1.0)
            if max(local, external) > cfg.bg_walkaway_threshold:
                factor, widen = 0.3, 2.0
        centre = self.last_price + cfg.bg_anchor_strength * (self.index_price - self.last_price)
        half = s.bg_half_spread * widen
        size = self._floor_lot(s.bg_depth_btc * factor / cfg.bg_levels)
        if size >= cfg.lot_size:
            for i in range(cfg.bg_levels):
                offset = half + i * cfg.bg_level_gap
                for side, px in ((BUY, centre * (1 - offset)), (SELL, centre * (1 + offset))):
                    order = Order(self._next_oid(), BG_ID, side, size,
                                  self.book.round_price(px), "GTC", step=t)
                    self._apply_fills(self.book.submit(order), stats)

    # ---- liquidation engine -----------------------------------------------
    def _run_liquidations(self, t: int, stats: _Stats) -> list:
        """Force-close every account below the maintenance requirement. Each
        forced sale hits the book, which can drag the mark price and put the
        next account under water: that chain reaction is the cascade. Each
        account is tried at most once per step."""
        mm = self.cfg.maintenance_margin_rate
        attempted, events = set(), []
        for _ in range(self.cfg.max_liquidation_rounds):
            mark = self.mark_price()
            breached = [a for a in self.accounts.values()
                        if a.liquidatable and a.id not in attempted and a.is_breached(mark, mm)]
            if not breached:
                break
            breached.sort(key=lambda a: a.margin_buffer(mark, mm))      # worst first
            for acct in breached:
                mark = self.mark_price()
                if not acct.is_breached(mark, mm):
                    continue
                attempted.add(acct.id)
                events.append(self._liquidate(acct, t, mark, stats))
        return events

    def _liquidate(self, acct: Account, t: int, mark: float, stats: _Stats) -> LiquidationEvent:
        self.book.cancel_account(acct.id)
        was_long = acct.position > 0
        qty = abs(acct.position)
        order = Order(self._next_oid(), acct.id, SELL if was_long else BUY, qty,
                      None, "IOC", True, t)
        fills = self.book.submit(order)
        filled = sum(f.qty for f in fills)
        value = sum(f.qty * f.price for f in fills)
        self._apply_fills(fills, stats)
        acct.n_liquidations += 1
        event = LiquidationEvent(t, acct.id, "long" if was_long else "short", qty, filled,
                                 value / filled if filled else 0.0, value, mark)
        self.liquidation_log.append(event)
        return event

    # ---- funding ----------------------------------------------------------
    def _sample_premium(self) -> None:
        """Record how far the exchange price sits from the index this step."""
        mid = self.book.mid()
        price = mid if mid is not None else self.last_price
        self.premium_buffer.append((price - self.index_price) / self.index_price)

    def _settle_funding(self) -> None:
        """Hourly cash transfer between longs and shorts. If the exchange price
        has been above the index, longs pay shorts (and the reverse). Every
        dollar one side pays, the other receives, so it is zero-sum across all
        accounts. It matters here because it drains the crowded side's
        collateral between liquidations."""
        cfg = self.cfg
        rate = self._funding_rate_now()
        hours = cfg.funding_interval_steps * cfg.hours_per_step
        mark = self.mark_price()
        for a in self.accounts.values():
            payment = a.position * mark * rate * hours
            a.collateral -= payment
            a.funding_paid += payment
        self.last_funding_rate = rate
        self.premium_buffer.clear()

    def _sweep_bad_debt(self) -> None:
        """If a closed account owes more than its cash, nobody can collect:
        record it as bad debt (auto-deleveraging is a Phase-2 intervention, not
        modelled here)."""
        for a in self.accounts.values():
            if not a.background and a.position == 0 and a.collateral < 0:
                self.bad_debt += -a.collateral
                a.collateral = 0.0

    # ---- bookkeeping ------------------------------------------------------
    def _make_record(self, t, stats, events, settled, prev_last, prev_index) -> StepRecord:
        cfg, mm = self.cfg, self.cfg.maintenance_margin_rate
        mark = self.mark_price()
        bid, ask = self.book.best_bid(), self.book.best_ask()
        mid = self.book.mid()
        depth_bid, depth_ask = self.book.depth(cfg.depth_band)

        total_value = near_value = 0.0
        min_buffer = None
        for a in self.accounts.values():
            if a.background or a.position == 0:
                continue
            value = a.notional(mark)
            buffer = a.margin_buffer(mark, mm)
            total_value += value
            if buffer is not None:
                if buffer < cfg.near_liq_buffer:
                    near_value += value
                min_buffer = buffer if min_buffer is None else min(min_buffer, buffer)

        return StepRecord(
            step=t, minute=t * cfg.step_minutes,
            index_price=self.index_price, last_price=self.last_price,
            mark_price=mark, mark_basis=(mark - self.index_price) / self.index_price,
            best_bid=bid or 0.0, best_ask=ask or 0.0,
            spread=(ask - bid) if mid is not None else 0.0,
            spread_rel=((ask - bid) / mid) if mid else 0.0,
            depth_bid=depth_bid, depth_ask=depth_ask,
            top_bid_size=self.book.top_size(BUY), top_ask_size=self.book.top_size(SELL),
            volume=stats.volume, signed_volume=stats.signed,
            order_flow_imbalance=(stats.signed / stats.volume) if stats.volume else 0.0,
            n_trades=stats.n_trades,
            open_interest=self._open_interest(),
            funding_rate_est=self._funding_rate_now(),
            last_funding_rate=self.last_funding_rate,
            funding_settled=settled,
            liq_count=len(events),
            liq_notional=sum(e.notional for e in events),
            liq_qty_long=sum(e.qty_filled for e in events if e.side_closed == "long"),
            liq_qty_short=sum(e.qty_filled for e in events if e.side_closed == "short"),
            price_return=math.log(self.last_price / prev_last) if prev_last > 0 else 0.0,
            index_return=math.log(self.index_price / prev_index) if prev_index > 0 else 0.0,
            n_agent_orders=stats.n_orders, n_agent_buys=stats.n_buys,
            n_agent_sells=stats.n_sells, n_rejected_orders=stats.n_rejected,
            oi_near_liq_share=(near_value / total_value) if total_value else 0.0,
            min_margin_buffer=min_buffer if min_buffer is not None else 0.0,
            bad_debt_total=self.bad_debt,
        )
