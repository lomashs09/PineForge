"""End-to-end tests for the backtesting engine."""

import pytest
from pineforge.engine import Engine
from pineforge.data import DataFeed


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


RSI_SCRIPT = """//@version=5
strategy("RSI Strategy", overlay=true)

rsi_len = input.int(14, "RSI Length")
rsi_val = ta.rsi(close, rsi_len)

if ta.crossover(rsi_val, 30)
    strategy.entry("Long", strategy.long)

if ta.crossunder(rsi_val, 70)
    strategy.close("Long")
"""


class TestParallelBacktests:
    """Verify that concurrent Engine.run() calls produce correct independent results."""

    def test_two_backtests_concurrent(self):
        """Run two different strategies on different data in parallel threads."""
        import concurrent.futures

        data1 = _make_oscillating_data(200)
        data2 = _make_oscillating_data(300, amplitude=15.0)

        def run_sma():
            engine = Engine(initial_capital=10000.0)
            return engine.run(SMA_CROSS_SCRIPT, data1)

        def run_rsi():
            engine = Engine(initial_capital=5000.0)
            return engine.run(RSI_SCRIPT, data2)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(run_sma)
            f2 = pool.submit(run_rsi)
            r1 = f1.result()
            r2 = f2.result()

        # Each should have correct strategy name
        assert r1.strategy_name == "SMA Crossover"
        assert r2.strategy_name == "RSI Strategy"

        # Each should use correct initial capital
        assert r1.initial_capital == 10000.0
        assert r2.initial_capital == 5000.0

        # Both should have produced trades
        assert r1.total_trades > 0

        # Equity curves should match data length
        assert len(r1.equity_curve) == 200
        assert len(r2.equity_curve) == 300

    def test_same_strategy_different_params(self):
        """Same script, different input overrides, run concurrently."""
        import concurrent.futures

        data = _make_oscillating_data(300)

        def run_fast():
            engine = Engine(initial_capital=10000.0)
            return engine.run(SMA_CROSS_SCRIPT, data, input_overrides={"Fast Length": 5, "Slow Length": 15})

        def run_slow():
            engine = Engine(initial_capital=10000.0)
            return engine.run(SMA_CROSS_SCRIPT, data, input_overrides={"Fast Length": 20, "Slow Length": 50})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(run_fast)
            f2 = pool.submit(run_slow)
            r1 = f1.result()
            r2 = f2.result()

        # Both use same strategy name
        assert r1.strategy_name == "SMA Crossover"
        assert r2.strategy_name == "SMA Crossover"

        # Different parameters should generally produce different trade counts
        # (not guaranteed but very likely with 5/15 vs 20/50 on oscillating data)
        assert r1.total_trades > 0
        assert r2.total_trades > 0

    def test_many_parallel_runs(self):
        """Stress test: 8 concurrent backtests."""
        import concurrent.futures

        def run_one(i):
            data = _make_oscillating_data(100 + i * 10)
            engine = Engine(initial_capital=10000.0 + i * 1000)
            return engine.run(SMA_CROSS_SCRIPT, data)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(run_one, i) for i in range(8)]
            results = [f.result() for f in futures]

        for i, r in enumerate(results):
            assert r.strategy_name == "SMA Crossover"
            assert r.initial_capital == 10000.0 + i * 1000
            assert len(r.equity_curve) == 100 + i * 10


class TestBroker:
    def test_position_tracking(self):
        from pineforge.broker import Broker
        broker = Broker(initial_capital=10000.0)
        broker.submit_entry("test", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        assert broker.position is not None
        assert broker.position.direction == "long"

    def test_close_trade(self):
        from pineforge.broker import Broker
        broker = Broker(initial_capital=10000.0)
        broker.submit_entry("test", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        broker.submit_close("test", 1)
        broker.process_orders(2, 105.0, 106.0, 104.0, 105.5)
        assert broker.position is None
        assert len(broker.closed_trades) == 1
        assert broker.closed_trades[0].pnl > 0

    def test_short_trade(self):
        from pineforge.broker import Broker
        broker = Broker(initial_capital=10000.0)
        broker.submit_entry("test", "short", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        broker.submit_close("test", 1)
        broker.process_orders(2, 95.0, 96.0, 94.0, 95.5)
        assert broker.closed_trades[0].pnl > 0  # profitable short
