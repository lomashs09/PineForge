"""End-to-end tests for the backtesting engine."""

import pytest
from strateg.engine import Engine
from strateg.data import DataFeed


def _make_trending_data(n: int = 100, start: float = 100.0, trend: float = 0.5) -> DataFeed:
    """Generate data that dips first then trends up, ensuring a crossover."""
    import math
    bars = []
    for i in range(n):
        # Dip for first 40 bars, then trend up — ensures a SMA crossover
        if i < 40:
            mid = start - 5 * math.sin(math.pi * i / 40)
        else:
            mid = start + trend * (i - 40)
        noise = ((i * 7 + 3) % 11 - 5) * 0.15
        mid += noise
        o = mid - 0.3
        c = mid + 0.3
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        bars.append({"open": round(o, 2), "high": round(h, 2), "low": round(l, 2),
                      "close": round(c, 2), "volume": 1000, "date": f"2024-01-{i+1:02d}"})
    return DataFeed(bars)


def _make_oscillating_data(n: int = 200, base: float = 100.0, amplitude: float = 10.0) -> DataFeed:
    """Generate oscillating OHLCV data (sine wave)."""
    import math
    bars = []
    for i in range(n):
        mid = base + amplitude * math.sin(i / 15.0)
        o = mid - 0.5
        c = mid + 0.5
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        bars.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000, "date": None})
    return DataFeed(bars)


SMA_CROSS_SCRIPT = """//@version=5
strategy("SMA Crossover", overlay=true)

length_fast = input.int(10, "Fast Length")
length_slow = input.int(30, "Slow Length")

fast_sma = ta.sma(close, length_fast)
slow_sma = ta.sma(close, length_slow)

if ta.crossover(fast_sma, slow_sma)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fast_sma, slow_sma)
    strategy.close("Long")
"""


class TestSMACrossover:
    def test_runs_without_error(self):
        data = _make_oscillating_data(200)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(SMA_CROSS_SCRIPT, data)
        assert result.initial_capital == 10000.0
        assert result.strategy_name == "SMA Crossover"
        assert len(result.equity_curve) > 0

    def test_produces_trades(self):
        data = _make_oscillating_data(200)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(SMA_CROSS_SCRIPT, data)
        assert result.total_trades > 0

    def test_equity_curve_length(self):
        data = _make_oscillating_data(200)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(SMA_CROSS_SCRIPT, data)
        assert len(result.equity_curve) == 200

    def test_with_commission(self):
        data = _make_oscillating_data(200)
        engine_no_comm = Engine(initial_capital=10000.0, commission=0.0)
        engine_comm = Engine(initial_capital=10000.0, commission=0.01)
        r1 = engine_no_comm.run(SMA_CROSS_SCRIPT, data)
        r2 = engine_comm.run(SMA_CROSS_SCRIPT, data)
        if r1.total_trades > 0 and r2.total_trades > 0:
            assert r2.net_profit <= r1.net_profit


class TestTrendingMarket:
    def test_profitable_in_uptrend(self):
        data = _make_trending_data(200, start=100.0, trend=0.3)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(SMA_CROSS_SCRIPT, data)
        assert result.net_profit > 0

    def test_summary_output(self):
        data = _make_trending_data(100)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(SMA_CROSS_SCRIPT, data)
        summary = result.summary()
        assert "SMA Crossover" in summary
        assert "Initial Capital" in summary

    def test_trade_log_output(self):
        data = _make_trending_data(100)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(SMA_CROSS_SCRIPT, data)
        log = result.trade_log()
        assert isinstance(log, str)


class TestSimpleStrategy:
    def test_always_long(self):
        source = """//@version=5
strategy("Always Long")
if bar_index == 0
    strategy.entry("Long", strategy.long)
"""
        data = _make_trending_data(50)
        engine = Engine(initial_capital=10000.0)
        result = engine.run(source, data)
        assert result.total_trades >= 1


class TestBroker:
    def test_position_tracking(self):
        from strateg.broker import Broker
        broker = Broker(initial_capital=10000.0)
        broker.submit_entry("test", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        assert broker.position is not None
        assert broker.position.direction == "long"

    def test_close_trade(self):
        from strateg.broker import Broker
        broker = Broker(initial_capital=10000.0)
        broker.submit_entry("test", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        broker.submit_close("test", 1)
        broker.process_orders(2, 105.0, 106.0, 104.0, 105.5)
        assert broker.position is None
        assert len(broker.closed_trades) == 1
        assert broker.closed_trades[0].pnl > 0

    def test_short_trade(self):
        from strateg.broker import Broker
        broker = Broker(initial_capital=10000.0)
        broker.submit_entry("test", "short", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        broker.submit_close("test", 1)
        broker.process_orders(2, 95.0, 96.0, 94.0, 95.5)
        assert broker.closed_trades[0].pnl > 0  # profitable short
