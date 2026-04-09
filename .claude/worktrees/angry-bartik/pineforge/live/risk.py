"""Risk manager — position sizing, daily loss limits, exposure control."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("pineforge.live.risk")


@dataclass
class RiskManager:
    """Guards against excessive risk in live trading."""

    # Limits
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 1
    cooldown_seconds: int = 60
    max_lot_size: float = 0.1
    min_lot_size: float = 0.01

    # State
    _daily_pnl: float = 0.0
    _starting_balance: float = 0.0
    _last_trade_time: float = 0.0
    _trade_count_today: int = 0
    _halted: bool = False
    _halt_reason: str = ""

    def reset_daily(self, balance: float) -> None:
        """Reset daily tracking (call at the start of each trading day)."""
        self._daily_pnl = 0.0
        self._starting_balance = balance
        self._trade_count_today = 0
        self._halted = False
        self._halt_reason = ""
        logger.info("Daily risk reset. Starting balance: %.2f", balance)

    def record_trade_pnl(self, pnl: float) -> None:
        """Record a closed trade's PnL for daily tracking."""
        self._daily_pnl += pnl
        self._trade_count_today += 1
        logger.info("Trade PnL: %.2f | Daily PnL: %.2f | Trades today: %d",
                     pnl, self._daily_pnl, self._trade_count_today)

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def check_can_trade(self, current_open_positions: int) -> tuple[bool, str]:
        """Check if a new trade is allowed.

        Returns:
            (allowed, reason) — reason is empty if allowed.
        """
        if self._halted:
            return False, f"Trading halted: {self._halt_reason}"

        # Daily loss limit
        if self._starting_balance > 0:
            daily_loss_pct = abs(min(self._daily_pnl, 0)) / self._starting_balance * 100
            if daily_loss_pct >= self.max_daily_loss_pct:
                self._halted = True
                self._halt_reason = f"Daily loss limit reached: {daily_loss_pct:.1f}% >= {self.max_daily_loss_pct}%"
                logger.warning(self._halt_reason)
                return False, self._halt_reason

        # Max open positions
        if current_open_positions >= self.max_open_positions:
            return False, f"Max open positions reached: {current_open_positions}/{self.max_open_positions}"

        # Cooldown between trades
        elapsed = time.time() - self._last_trade_time
        if elapsed < self.cooldown_seconds and self._last_trade_time > 0:
            remaining = int(self.cooldown_seconds - elapsed)
            return False, f"Cooldown active: {remaining}s remaining"

        return True, ""

    def calculate_lot_size(self, balance: float, stop_distance_price: float | None = None) -> float:
        """Calculate position size based on risk parameters.

        If stop_distance_price is provided, sizes the position so that hitting
        the stop would lose risk_per_trade_pct of balance. Otherwise, uses
        a simple percentage of balance approach.
        """
        if balance <= 0:
            return self.min_lot_size

        risk_amount = balance * (self.risk_per_trade_pct / 100.0)

        if stop_distance_price and stop_distance_price > 0:
            # For Gold: 1 lot = 100 oz, price move of $1 = $100 per lot
            lot_size = risk_amount / (stop_distance_price * 100)
        else:
            # Default: use a conservative fixed fraction
            lot_size = self.min_lot_size

        # Clamp to limits
        lot_size = max(self.min_lot_size, min(lot_size, self.max_lot_size))

        # Round to 2 decimal places (standard lot precision)
        lot_size = round(lot_size, 2)

        return lot_size

    def record_trade_opened(self) -> None:
        """Mark that a trade was just opened (for cooldown tracking)."""
        self._last_trade_time = time.time()

    def status_summary(self) -> str:
        """Return a human-readable risk status."""
        lines = [
            f"  Daily PnL:          {self._daily_pnl:>10.2f}",
            f"  Trades today:       {self._trade_count_today:>10}",
            f"  Halted:             {'YES - ' + self._halt_reason if self._halted else 'No'}",
        ]
        if self._starting_balance > 0 and self._daily_pnl < 0:
            loss_pct = abs(self._daily_pnl) / self._starting_balance * 100
            lines.append(f"  Daily loss:         {loss_pct:>9.1f}% / {self.max_daily_loss_pct}% max")
        return "\n".join(lines)
