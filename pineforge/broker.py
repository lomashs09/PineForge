"""Simulated broker for strategy backtesting.

Handles order submission, fill execution, position tracking, and PnL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Trade:
    entry_id: str
    direction: str  # "long" or "short"
    qty: float
    entry_price: float
    entry_bar: int
    entry_date: Any = None
    exit_price: float | None = None
    exit_bar: int | None = None
    exit_date: Any = None
    pnl: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None


@dataclass
class PendingOrder:
    id: str
    action: str  # "entry", "close", "close_all", "exit"
    direction: str = ""
    qty: float = 0.0
    bar_index: int = 0
    stop: float | None = None
    limit: float | None = None
    from_entry: str | None = None


class Broker:
    """Simulates order execution with configurable fill behavior."""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        fill_on: str = "next_open",
    ):
        self.initial_capital = initial_capital
        self.equity = initial_capital
        self.cash = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.fill_on = fill_on  # "next_open" or "close"

        self.position: Trade | None = None
        self.closed_trades: list[Trade] = []
        self.pending_orders: list[PendingOrder] = []
        self._exit_orders: list[PendingOrder] = []
        self.equity_curve: list[float] = []
        self._realized_pnl: float = 0.0  # IMP 3: O(1) running total

    @property
    def position_size(self) -> float:
        if self.position is None:
            return 0.0
        return self.position.qty if self.position.direction == "long" else -self.position.qty

    def submit_entry(self, id: str, direction: str, qty: float, bar_index: int) -> None:
        self.pending_orders.append(PendingOrder(
            id=id, action="entry", direction=direction, qty=qty, bar_index=bar_index,
        ))

    def submit_close(self, id: str, bar_index: int) -> None:
        self.pending_orders.append(PendingOrder(
            id=id, action="close", bar_index=bar_index,
        ))

    def submit_close_all(self, bar_index: int) -> None:
        self.pending_orders.append(PendingOrder(
            id="__all__", action="close_all", bar_index=bar_index,
        ))

    def submit_exit(
        self, id: str, from_entry: str | None = None,
        stop: float | None = None, limit: float | None = None,
        bar_index: int = 0,
    ) -> None:
        order = PendingOrder(
            id=id, action="exit", from_entry=from_entry,
            stop=stop, limit=limit, bar_index=bar_index,
        )
        # Replace any existing exit with the same id (Pine Script semantics:
        # calling strategy.exit() again with the same id updates the order).
        self._exit_orders = [o for o in self._exit_orders if o.id != id]
        self._exit_orders.append(order)

    def process_orders(
        self,
        bar_index: int,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        date: Any = None,
    ) -> None:
        """Process all pending orders against the current bar's prices.

        TradingView semantics:
        1. Close orders apply only to positions that existed BEFORE this bar.
        2. Entry orders fill at the open (may flip/reverse positions).
        3. Persistent exit orders (stop/limit) evaluate against the bar's OHLC.
        """
        fill_price = open_price if self.fill_on == "next_open" else close_price

        orders = list(self.pending_orders)
        self.pending_orders.clear()

        # Phase 1: process close/close_all orders against the pre-existing position.
        for order in orders:
            if order.action in ("close", "close_all"):
                if order.action == "close":
                    self._fill_close(order, fill_price, bar_index, date)
                else:
                    self._fill_close_all(fill_price, bar_index, date)

        # Phase 2: process entry orders (may open new position or flip direction).
        for order in orders:
            if order.action == "entry":
                self._fill_entry(order, fill_price, bar_index, date)

        # Phase 3: evaluate persistent exit orders (stop/limit) against this bar's OHLC.
        if self.position is not None and self._exit_orders:
            for exit_order in list(self._exit_orders):
                if self.position is None:
                    break
                self._process_exit(exit_order, open_price, high_price, low_price, close_price, bar_index, date)

        self._update_equity(close_price)

    def _fill_entry(self, order: PendingOrder, price: float, bar_index: int, date: Any) -> None:
        # BUG 9 fix: when flipping direction, close old position with its OWN
        # slippage (adverse to the direction being closed), then open the new
        # position with its own adverse slippage.  Previously both used the
        # same adj_price, which was wrong.
        if self.position is not None:
            if self.position.direction != order.direction:
                close_slip = (
                    price - self.slippage  # long exit: lower is worse
                    if self.position.direction == "long"
                    else price + self.slippage  # short exit: higher is worse
                )
                self._close_position(close_slip, bar_index, date)
            else:
                return  # same direction: already in position, no-op

        # Entry fill with adverse slippage.
        adj_price = price + self.slippage if order.direction == "long" else price - self.slippage

        cost = adj_price * order.qty * self.commission
        self.cash -= cost

        self.position = Trade(
            entry_id=order.id,
            direction=order.direction,
            qty=order.qty,
            entry_price=adj_price,
            entry_bar=bar_index,
            entry_date=date,
        )

    def _fill_close(self, order: PendingOrder, price: float, bar_index: int, date: Any) -> None:
        if self.position is None:
            return
        if order.id != "__all__" and self.position.entry_id != order.id:
            return
        adj_price = price - self.slippage if self.position.direction == "long" else price + self.slippage
        self._close_position(adj_price, bar_index, date)

    def _fill_close_all(self, price: float, bar_index: int, date: Any) -> None:
        if self.position is None:
            return
        adj_price = price - self.slippage if self.position.direction == "long" else price + self.slippage
        self._close_position(adj_price, bar_index, date)

    def _process_exit(
        self, order: PendingOrder,
        open_p: float, high_p: float, low_p: float, close_p: float,
        bar_index: int, date: Any,
    ) -> None:
        if self.position is None:
            return
        if order.from_entry and self.position.entry_id != order.from_entry:
            return

        is_long = self.position.direction == "long"

        # BUG 2 fix: apply adverse slippage to stop/limit fill prices, just
        # like regular close orders.  Stop exits worsen by slippage; limit
        # exits also get a small amount of adverse slippage.
        if order.stop is not None:
            if is_long and low_p <= order.stop:
                raw = min(open_p, order.stop)
                self._close_position(raw - self.slippage, bar_index, date)
                return
            if not is_long and high_p >= order.stop:
                raw = max(open_p, order.stop)
                self._close_position(raw + self.slippage, bar_index, date)
                return

        if order.limit is not None:
            if is_long and high_p >= order.limit:
                raw = max(open_p, order.limit)
                self._close_position(raw - self.slippage, bar_index, date)
                return
            if not is_long and low_p <= order.limit:
                raw = min(open_p, order.limit)
                self._close_position(raw + self.slippage, bar_index, date)
                return

    def _close_position(self, exit_price: float, bar_index: int, date: Any) -> None:
        if self.position is None:
            return
        pos = self.position
        if pos.direction == "long":
            pnl = (exit_price - pos.entry_price) * pos.qty
        else:
            pnl = (pos.entry_price - exit_price) * pos.qty

        commission = exit_price * pos.qty * self.commission
        pnl -= commission

        pos.exit_price = exit_price
        pos.exit_bar = bar_index
        pos.exit_date = date
        pos.pnl = pnl

        self.cash += pnl
        self._realized_pnl += pnl  # IMP 3: maintain running total
        self.closed_trades.append(pos)
        self.position = None
        self._exit_orders.clear()

    def _update_equity(self, close_price: float) -> None:
        unrealized = 0.0
        if self.position is not None:
            if self.position.direction == "long":
                unrealized = (close_price - self.position.entry_price) * self.position.qty
            else:
                unrealized = (self.position.entry_price - close_price) * self.position.qty
        # IMP 3: use O(1) running total instead of O(n) sum over all trades
        self.equity = self.initial_capital + self._realized_pnl + unrealized
        self.equity_curve.append(self.equity)
