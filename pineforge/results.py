"""Backtest results computation and reporting."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .broker import Broker, Trade


@dataclass
class BacktestResult:
    strategy_name: str = ""
    initial_capital: float = 10000.0
    final_equity: float = 10000.0

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0

    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_winning_trade: float = 0.0
    avg_losing_trade: float = 0.0

    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def summary(self) -> str:
        sep = "=" * 50
        lines = [
            sep,
            f"  Strategy: {self.strategy_name}",
            sep,
            f"  Initial Capital:     {self.initial_capital:>14,.2f}",
            f"  Final Equity:        {self.final_equity:>14,.2f}",
            f"  Net Profit:          {self.net_profit:>14,.2f}",
            f"  Total Return:        {self.total_return_pct:>13.2f}%",
            "",
            f"  Total Trades:        {self.total_trades:>14}",
            f"  Winning Trades:      {self.winning_trades:>14}",
            f"  Losing Trades:       {self.losing_trades:>14}",
            f"  Win Rate:            {self.win_rate:>13.2f}%",
            "",
            f"  Gross Profit:        {self.gross_profit:>14,.2f}",
            f"  Gross Loss:          {self.gross_loss:>14,.2f}",
            f"  Profit Factor:       {self.profit_factor:>14.2f}",
            "",
            f"  Avg Trade PnL:       {self.avg_trade_pnl:>14,.2f}",
            f"  Avg Winning Trade:   {self.avg_winning_trade:>14,.2f}",
            f"  Avg Losing Trade:    {self.avg_losing_trade:>14,.2f}",
            "",
            f"  Max Drawdown:        {self.max_drawdown:>14,.2f}",
            f"  Max Drawdown %:      {self.max_drawdown_pct:>13.2f}%",
            f"  Sharpe Ratio:        {self.sharpe_ratio:>14.2f}",
            sep,
        ]
        return "\n".join(lines)

    def trade_log(self) -> str:
        if not self.trades:
            return "No trades."
        header = f"{'#':>4}  {'Direction':>9}  {'Entry Price':>12}  {'Exit Price':>11}  {'PnL':>12}  {'Entry Date':<20}  {'Exit Date':<20}"
        sep = "-" * len(header)
        lines = [header, sep]
        for i, t in enumerate(self.trades, 1):
            entry_date = str(t.entry_date)[:19] if t.entry_date else "N/A"
            exit_date = str(t.exit_date)[:19] if t.exit_date else "N/A"
            lines.append(
                f"{i:>4}  {t.direction:>9}  {t.entry_price:>12,.2f}  "
                f"{t.exit_price or 0:>11,.2f}  {t.pnl:>12,.2f}  "
                f"{entry_date:<20}  {exit_date:<20}"
            )
        return "\n".join(lines)


def compute_results(broker: Broker, initial_capital: float, strategy_name: str = "") -> BacktestResult:
    trades = broker.closed_trades
    equity_curve = broker.equity_curve

    total = len(trades)
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]

    gross_profit = sum(t.pnl for t in winners)
    gross_loss = sum(t.pnl for t in losers)
    net_profit = gross_profit + gross_loss

    final_equity = initial_capital + net_profit

    win_rate = (len(winners) / total * 100) if total > 0 else 0.0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else float("inf") if gross_profit > 0 else 0.0
    total_return_pct = (net_profit / initial_capital * 100) if initial_capital > 0 else 0.0

    avg_trade = net_profit / total if total > 0 else 0.0
    avg_win = gross_profit / len(winners) if winners else 0.0
    avg_loss = gross_loss / len(losers) if losers else 0.0

    max_dd, max_dd_pct = _compute_max_drawdown(equity_curve)
    sharpe = _compute_sharpe(equity_curve)

    return BacktestResult(
        strategy_name=strategy_name,
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_trades=total,
        winning_trades=len(winners),
        losing_trades=len(losers),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=net_profit,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_return_pct=total_return_pct,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        avg_trade_pnl=avg_trade,
        avg_winning_trade=avg_win,
        avg_losing_trade=avg_loss,
        trades=trades,
        equity_curve=equity_curve,
    )


def _compute_max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    if not equity_curve:
        return 0.0, 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = (dd / peak * 100) if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct
    return max_dd, max_dd_pct


def _compute_sharpe(equity_curve: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio from equity curve returns."""
    if len(equity_curve) < 2:
        return 0.0
    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] != 0:
            returns.append(equity_curve[i] / equity_curve[i - 1] - 1.0)
    if not returns:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r == 0:
        return 0.0
    # Annualize assuming daily bars (252 trading days)
    return (mean_r - risk_free) / std_r * math.sqrt(252)
