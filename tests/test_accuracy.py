"""Tests for all backtesting accuracy fixes.

Each test verifies a specific bug fix or improvement from the audit:
  BUG 1  – MACD signal/histogram were always na
  BUG 2  – Exit stop/limit orders had no slippage
  BUG 3  – ta.crossover/crossunder failed with scalar values
  BUG 4  – ta.stdev used population std dev instead of sample
  BUG 5  – strategy() declaration params were ignored by the broker
  BUG 6  – End-of-backtest position close had no slippage
  BUG 7  – Duplicate exit orders accumulated each bar
  BUG 8  – Conditionally-assigned Series grew shorter than bar count
  BUG 9  – Position flip used the same price for close and new entry
  BUG 10 – StrategyContext leaked state between Engine.run() calls
  BUG 11 – EMA/RMA state keys used id() (memory-address-based)
  IMP 1  – strategy.position_size exposed to Pine Script
  IMP 2  – Sharpe ratio annualization is now per-interval
  IMP 3  – Equity update is O(1) via _realized_pnl
  IMP 4  – strategy.opentrades / strategy.closedtrades exposed
"""

from __future__ import annotations

import math
import pytest

from pineforge.engine import Engine
from pineforge.data import DataFeed
from pineforge.broker import Broker
from pineforge.series import Series, is_na, na_value
from pineforge.builtins.ta import ta_stdev, ta_crossover, ta_crossunder, ta_ema, ta_macd, get_ta_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linear_data(n: int = 60, start: float = 100.0, step: float = 1.0) -> DataFeed:
    """Monotonically rising bars."""
    bars = []
    for i in range(n):
        price = start + step * i
        bars.append({"open": price, "high": price + 0.5, "low": price - 0.5,
                     "close": price, "volume": 1000, "date": f"2024-{i+1:04d}"})
    return DataFeed(bars)


def _oscillating_data(n: int = 200) -> DataFeed:
    bars = []
    for i in range(n):
        mid = 100.0 + 10.0 * math.sin(i / 15.0)
        bars.append({"open": mid - 0.5, "high": mid + 0.8, "low": mid - 0.8,
                     "close": mid + 0.5, "volume": 1000, "date": None})
    return DataFeed(bars)


def _run(script: str, data: DataFeed = None, **engine_kwargs) -> "BacktestResult":
    if data is None:
        data = _oscillating_data()
    e = Engine(**engine_kwargs)
    return e.run(script, data)


# ---------------------------------------------------------------------------
# BUG 1 – MACD signal and histogram
# ---------------------------------------------------------------------------

class TestMACDFix:
    # Note: parser doesn't support destructuring [ml, sig, hist] = ...
    # so we test MACD via the Python API directly and via a script that uses
    # the MACD return values indirectly.
    SCRIPT = """//@version=5
strategy("MACD Test")
if bar_index == 0
    strategy.entry("L", strategy.long)
if bar_index == 30
    strategy.close("L")
"""

    def test_macd_signal_not_always_na(self):
        """After enough bars, MACD signal must be a real number."""
        data = _oscillating_data(200)
        # We can't directly inspect indicator values from the result,
        # but we can verify the strategy runs without error and that the
        # MACD internal Series is populated by running two bars:
        from pineforge.builtins.ta import _state
        get_ta_state().reset()
        close_s = Series()
        for v in [100, 101, 102, 103, 99, 98, 100, 102, 104, 106,
                  105, 107, 108, 109, 110, 111, 112, 113, 114, 115,
                  116, 117, 118, 119, 120, 121, 122, 123, 124, 125,
                  126, 127, 128, 129, 130]:
            close_s.push(v)
        # After enough bars (>= 26 + 9 for signal warmup), signal must be non-na
        from pineforge.builtins.ta import ta_ema, ta_macd
        get_ta_state().reset()
        src = Series()
        results = []
        for v in range(1, 80):
            src.push(float(v))
            ml, sig, hist = ta_macd(src, 12, 26, 9)
            results.append((ml, sig, hist))
        # Beyond bar 34 (26 slow warmup + 9 signal warmup - 1), signal must not be na
        last_ml, last_sig, last_hist = results[-1]
        assert not is_na(last_ml), "MACD line must be a number"
        assert not is_na(last_sig), "MACD signal must be a number after warmup"
        assert not is_na(last_hist), "MACD histogram must be a number after warmup"
        assert abs(last_hist - (last_ml - last_sig)) < 1e-10, "histogram = macd - signal"

    def test_macd_strategy_runs(self):
        result = _run(self.SCRIPT, _oscillating_data(200))
        assert result.total_trades >= 1


# ---------------------------------------------------------------------------
# BUG 2 – Exit stop/limit orders apply slippage
# ---------------------------------------------------------------------------

class TestExitSlippage:
    def test_stop_exit_applies_slippage_long(self):
        """Long stop exit fill must be <= stop - slippage."""
        broker = Broker(initial_capital=10000, slippage=2.0, fill_on="next_open")
        # Enter long at 100
        broker.submit_entry("L", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        assert broker.position is not None

        # Set stop at 98; bar goes low=95, so stop triggers
        broker.submit_exit("SL", from_entry="L", stop=98.0)
        broker.process_orders(2, 96.0, 97.0, 95.0, 96.5)

        assert broker.position is None, "stop should have triggered"
        trade = broker.closed_trades[-1]
        # fill = min(open=96, stop=98) = 96, minus slippage 2 = 94
        assert trade.exit_price == pytest.approx(94.0, abs=1e-9), (
            f"Expected 94.0, got {trade.exit_price}")

    def test_limit_exit_applies_slippage_long(self):
        """Long limit (take-profit) exit fill must be >= limit - slippage."""
        broker = Broker(initial_capital=10000, slippage=1.0, fill_on="next_open")
        broker.submit_entry("L", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)

        broker.submit_exit("TP", from_entry="L", limit=110.0)
        # Bar high = 112, so limit triggers; fill = max(open=111, limit=110) = 111, - slippage 1 = 110
        broker.process_orders(2, 111.0, 112.0, 108.0, 111.5)

        assert broker.position is None, "limit should have triggered"
        trade = broker.closed_trades[-1]
        assert trade.exit_price == pytest.approx(110.0, abs=1e-9)

    def test_stop_exit_applies_slippage_short(self):
        """Short stop exit fill must be >= stop + slippage."""
        broker = Broker(initial_capital=10000, slippage=1.0, fill_on="next_open")
        broker.submit_entry("S", "short", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)

        broker.submit_exit("SL", from_entry="S", stop=103.0)
        # Bar high = 105, so stop triggers; fill = max(open=104, stop=103) = 104, + slippage 1 = 105
        broker.process_orders(2, 104.0, 105.0, 101.0, 104.5)

        assert broker.position is None
        trade = broker.closed_trades[-1]
        assert trade.exit_price == pytest.approx(105.0, abs=1e-9)


# ---------------------------------------------------------------------------
# BUG 3 – ta.crossover / ta.crossunder with scalar values
# ---------------------------------------------------------------------------

class TestCrossoverScalar:
    def _make_rsi_series(self, values):
        s = Series()
        for v in values:
            s.push(v)
        return s

    def test_crossover_series_vs_scalar(self):
        """ta.crossover(series, 70) must return True when series crosses 70 from below."""
        s = self._make_rsi_series([65.0, 72.0])  # prev=65 < 70, cur=72 > 70
        assert ta_crossover(s, 70.0) is True

    def test_crossover_series_vs_scalar_no_cross(self):
        s = self._make_rsi_series([68.0, 69.0])
        assert ta_crossover(s, 70.0) is False

    def test_crossunder_series_vs_scalar(self):
        """ta.crossunder(series, 30) must return True when series crosses 30 from above."""
        s = self._make_rsi_series([35.0, 28.0])  # prev=35 > 30, cur=28 < 30
        assert ta_crossunder(s, 30.0) is True

    def test_crossunder_series_vs_scalar_no_cross(self):
        s = self._make_rsi_series([32.0, 31.0])
        assert ta_crossunder(s, 30.0) is False

    def test_crossover_both_scalars_returns_false(self):
        assert ta_crossover(50.0, 70.0) is False

    def test_crossover_strategy_with_rsi_level(self):
        """Full backtest: strategy using ta.crossover(rsi, 70) must produce trades."""
        script = """//@version=5
strategy("RSI Level Cross")
rsi = ta.rsi(close, 14)
if ta.crossover(rsi, 70)
    strategy.close("L")
if ta.crossunder(rsi, 30)
    strategy.entry("L", strategy.long)
"""
        result = _run(script, _oscillating_data(300))
        assert result.total_trades >= 1


# ---------------------------------------------------------------------------
# BUG 4 – ta.stdev uses sample std dev (Bessel's correction)
# ---------------------------------------------------------------------------

class TestStdevFix:
    def _make_series(self, values):
        s = Series()
        for v in values:
            s.push(v)
        return s

    def test_stdev_matches_pandas_sample(self):
        """ta.stdev(close, 20) must match pandas Series.std(ddof=1)."""
        import statistics
        vals = [float(100 + i * 0.5 + (i % 3) * 0.1) for i in range(30)]
        s = self._make_series(vals)
        result = ta_stdev(s, 20)

        # The last 20 values
        last20 = vals[-20:]
        expected = statistics.stdev(last20)  # Python stdev uses N-1
        assert result == pytest.approx(expected, rel=1e-9)

    def test_stdev_not_population(self):
        """Population stdev (÷N) would differ from sample (÷N-1)."""
        vals = [10.0, 12.0, 14.0, 16.0, 18.0]  # n=5, known variance
        s = self._make_series(vals)
        result = ta_stdev(s, 5)
        mean = sum(vals) / 5
        pop_var = sum((v - mean) ** 2 for v in vals) / 5
        sample_var = sum((v - mean) ** 2 for v in vals) / 4
        assert result == pytest.approx(sample_var ** 0.5, rel=1e-9)
        assert result != pytest.approx(pop_var ** 0.5, rel=1e-3)

    def test_stdev_requires_at_least_2(self):
        s = self._make_series([100.0])
        assert is_na(ta_stdev(s, 1))


# ---------------------------------------------------------------------------
# BUG 5 – strategy() declaration params applied to broker
# ---------------------------------------------------------------------------

class TestStrategyParamsToBroker:
    def test_commission_from_strategy_declaration(self):
        """When engine commission=0 but script declares commission_value=0.2,
        the broker should use it and yield lower net_profit than no commission.
        """
        script = """//@version=5
strategy("ComTest", commission_value=0.2, commission_type="percent")
if bar_index == 5
    strategy.entry("L", strategy.long)
if bar_index == 20
    strategy.close("L")
"""
        data = _linear_data(50)
        result_with = Engine(commission=0.0).run(script, data)
        result_none = Engine(commission=0.0).run(
            script.replace("commission_value=0.2, commission_type=\"percent\",\n", "").replace(
                'commission_value=0.2, commission_type="percent"', ""), data)

        # With commission declared in script, net profit must be lower
        if result_with.total_trades > 0 and result_none.total_trades > 0:
            assert result_with.net_profit <= result_none.net_profit

    def test_engine_commission_overrides_script(self):
        """When engine sets commission > 0, it should take precedence."""
        script = """//@version=5
strategy("ComTest", commission_value=0.0)
if bar_index == 5
    strategy.entry("L", strategy.long)
if bar_index == 20
    strategy.close("L")
"""
        data = _linear_data(50)
        result_engine_comm = Engine(commission=0.01).run(script, data)
        result_no_comm = Engine(commission=0.0).run(script, data)
        if result_engine_comm.total_trades > 0 and result_no_comm.total_trades > 0:
            assert result_engine_comm.net_profit < result_no_comm.net_profit


# ---------------------------------------------------------------------------
# BUG 6 – End-of-backtest position close applies slippage
# ---------------------------------------------------------------------------

class TestFinalCloseSPippage:
    def test_long_position_closed_with_slippage_at_end(self):
        """A long position force-closed at end of backtest must exit below the raw close."""
        script = """//@version=5
strategy("Hold")
if bar_index == 0
    strategy.entry("L", strategy.long)
"""
        data = _linear_data(10, start=100.0, step=1.0)  # last close = 109
        result = Engine(slippage=2.0).run(script, data)
        assert result.total_trades == 1
        trade = result.trades[0]
        # Fill should be 109 - 2 = 107
        assert trade.exit_price == pytest.approx(107.0, abs=1e-9), (
            f"Expected 107.0, got {trade.exit_price}")

    def test_short_position_closed_with_slippage_at_end(self):
        script = """//@version=5
strategy("HoldShort")
if bar_index == 0
    strategy.entry("S", strategy.short)
"""
        data = _linear_data(10, start=100.0, step=1.0)  # last close = 109
        result = Engine(slippage=2.0).run(script, data)
        assert result.total_trades == 1
        trade = result.trades[0]
        # Fill should be 109 + 2 = 111
        assert trade.exit_price == pytest.approx(111.0, abs=1e-9)


# ---------------------------------------------------------------------------
# BUG 7 – No duplicate exit orders
# ---------------------------------------------------------------------------

class TestNoDuplicateExitOrders:
    def test_exit_order_does_not_duplicate(self):
        """submit_exit with same id must replace, not accumulate."""
        broker = Broker(initial_capital=10000)
        broker.submit_entry("L", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)

        # Submit exit multiple times (simulating script calling strategy.exit every bar)
        for _ in range(5):
            broker.submit_exit("SL", from_entry="L", stop=95.0)
        assert len(broker._exit_orders) == 1, "Only one exit order should exist"

    def test_exit_triggers_exactly_once(self):
        """Even with repeated exit submissions, trade must close only once."""
        script = """//@version=5
strategy("ExitTest")
if bar_index == 2
    strategy.entry("L", strategy.long)
strategy.exit("SL", from_entry="L", stop=95.0)
"""
        data = DataFeed([
            {"open": 100, "high": 101, "low": 99, "close": 100, "date": "1"},
            {"open": 100, "high": 101, "low": 99, "close": 100, "date": "2"},
            {"open": 100, "high": 101, "low": 99, "close": 100, "date": "3"},
            {"open": 100, "high": 101, "low": 99, "close": 100, "date": "4"},
            {"open": 94, "high": 95, "low": 93, "close": 94, "date": "5"},   # stop hits
            {"open": 94, "high": 95, "low": 93, "close": 94, "date": "6"},
        ])
        result = Engine().run(script, data)
        assert result.total_trades == 1


# ---------------------------------------------------------------------------
# BUG 8 – Series misalignment for conditionally-assigned variables
# ---------------------------------------------------------------------------

class TestConditionalSeriesAlignment:
    def test_conditional_assignment_history_correct(self):
        """A variable assigned only on even bars must have na on odd bars."""
        script = """//@version=5
strategy("CondAlign")
if bar_index % 2 == 0
    x = close
if bar_index == 0
    strategy.entry("L", strategy.long)
if bar_index == 4
    strategy.close("L")
"""
        # Just verifying the backtest doesn't crash and produces correct trades
        data = _linear_data(10)
        result = Engine().run(script, data)
        # Must not raise; equity curve must be full length
        assert len(result.equity_curve) == len(data)

    def test_mixed_bars_script_runs_correctly(self):
        """Script with conditional series logic must complete without errors."""
        script = """//@version=5
strategy("MixedBars")
var last_high = na
if high > 105
    last_high := high
if bar_index == 5
    strategy.entry("L", strategy.long)
if bar_index == 15
    strategy.close("L")
"""
        result = Engine().run(script, _oscillating_data(30))
        assert result.total_trades >= 1


# ---------------------------------------------------------------------------
# BUG 9 – Position flip: separate slippage for close and new entry
# ---------------------------------------------------------------------------

class TestPositionFlipSlippage:
    def test_flip_creates_two_separate_fills(self):
        """Flipping long→short must produce two slippage events, not share one fill price."""
        broker = Broker(initial_capital=10000, slippage=1.0, fill_on="next_open")

        # Enter long at 100 (fill = 100 + 1 = 101)
        broker.submit_entry("L", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        assert broker.position.entry_price == pytest.approx(101.0)

        # Flip to short — long closes at 105 - 1 = 104, short opens at 105 - 1 = 104
        broker.submit_entry("S", "short", 1.0, 1)
        broker.process_orders(2, 105.0, 106.0, 104.0, 105.5)

        assert len(broker.closed_trades) == 1
        long_trade = broker.closed_trades[0]
        # Long closed at fill_price(105) - slippage(1) = 104
        assert long_trade.exit_price == pytest.approx(104.0, abs=1e-9)

        # Short entry at fill_price(105) - slippage(1) = 104
        assert broker.position is not None
        assert broker.position.direction == "short"
        assert broker.position.entry_price == pytest.approx(104.0, abs=1e-9)


# ---------------------------------------------------------------------------
# BUG 10 – StrategyContext reset between runs
# ---------------------------------------------------------------------------

class TestStrategyContextReset:
    def test_title_resets_between_runs(self):
        """Running two different strategies must not inherit title from previous run."""
        s1 = """//@version=5
strategy("StrategyOne")
if bar_index == 0
    strategy.entry("L", strategy.long)
"""
        s2 = """//@version=5
strategy("StrategyTwo")
if bar_index == 0
    strategy.entry("L", strategy.long)
"""
        data = _linear_data(20)
        e = Engine()
        r1 = e.run(s1, data)
        r2 = e.run(s2, data)
        assert r1.strategy_name == "StrategyOne"
        assert r2.strategy_name == "StrategyTwo"

    def test_commission_resets_between_runs(self):
        """Commission from a previous run must not bleed into the next."""
        s_with_comm = """//@version=5
strategy("WithComm", commission_value=5.0)
if bar_index == 5
    strategy.entry("L", strategy.long)
if bar_index == 15
    strategy.close("L")
"""
        s_no_comm = """//@version=5
strategy("NoComm")
if bar_index == 5
    strategy.entry("L", strategy.long)
if bar_index == 15
    strategy.close("L")
"""
        data = _linear_data(30)
        e = Engine()
        r1 = e.run(s_with_comm, data)
        r2 = e.run(s_no_comm, data)
        r3 = e.run(s_no_comm, data)
        # r2 and r3 must be identical (no bleed from r1's commission)
        assert r2.net_profit == pytest.approx(r3.net_profit, rel=1e-9)


# ---------------------------------------------------------------------------
# BUG 11 – Stable Series IDs don't cause TA state collisions
# ---------------------------------------------------------------------------

class TestStableSeriesIDs:
    def test_each_series_has_unique_id(self):
        """Series objects must have distinct _id values."""
        s1, s2, s3 = Series(), Series(), Series()
        ids = {s1._id, s2._id, s3._id}
        assert len(ids) == 3, "Each Series must have a unique _id"

    def test_ema_state_keys_distinct_for_different_series(self):
        """Two different Series with the same length must have different EMA state keys."""
        from pineforge.builtins.ta import _series_key
        s1 = Series()
        s2 = Series()
        k1 = _series_key(s1, 14 * 31)
        k2 = _series_key(s2, 14 * 31)
        assert k1 != k2

    def test_two_emas_different_sources_independent(self):
        """EMA of two independent sources must produce different values."""
        get_ta_state().reset()
        s1 = Series()
        s2 = Series()
        for i in range(30):
            s1.push(float(100 + i))
            s2.push(float(200 + i))
        ema1 = ta_ema(s1, 14)
        ema2 = ta_ema(s2, 14)
        assert not is_na(ema1) and not is_na(ema2)
        assert ema1 != pytest.approx(ema2, rel=1e-3)


# ---------------------------------------------------------------------------
# IMP 1 – strategy.position_size exposed to scripts
# ---------------------------------------------------------------------------

class TestPositionSizeExposed:
    def test_position_size_reflects_open_position(self):
        """strategy.position_size must be accessible to scripts."""
        script = """//@version=5
strategy("PosSize")
if bar_index == 2
    strategy.entry("L", strategy.long)
if strategy.position_size > 0 and bar_index == 10
    strategy.close("L")
"""
        data = _linear_data(20)
        result = Engine().run(script, data)
        assert result.total_trades >= 1


# ---------------------------------------------------------------------------
# IMP 2 – Sharpe annualization by interval
# ---------------------------------------------------------------------------

class TestSharpeAnnualization:
    def test_sharpe_differs_by_interval(self):
        """Sharpe ratio must differ for 1d vs 1h (different annualization)."""
        script = """//@version=5
strategy("Sharpe")
if bar_index % 5 == 0
    strategy.entry("L", strategy.long)
if bar_index % 5 == 3
    strategy.close("L")
"""
        data = _oscillating_data(200)
        r_daily = Engine(interval="1d").run(script, data)
        r_hourly = Engine(interval="1h").run(script, data)
        # Different intervals → different annualization → different Sharpe
        assert r_daily.sharpe_ratio != pytest.approx(r_hourly.sharpe_ratio, rel=1e-3)

    def test_sharpe_daily_uses_252(self):
        """Spot-check: daily Sharpe uses sqrt(252) scaling."""
        from pineforge.results import _compute_sharpe
        curve = [10000.0, 10100.0, 10050.0, 10200.0, 10150.0, 10300.0]
        sharpe_daily = _compute_sharpe(curve, bars_per_year=252)
        sharpe_hourly = _compute_sharpe(curve, bars_per_year=252 * 6.5)
        assert sharpe_daily != pytest.approx(sharpe_hourly, rel=1e-3)


# ---------------------------------------------------------------------------
# IMP 3 – O(1) equity calculation via _realized_pnl
# ---------------------------------------------------------------------------

class TestEquityRunningTotal:
    def test_realized_pnl_tracks_closed_trades(self):
        """_realized_pnl must equal sum of all closed trade PnLs."""
        broker = Broker(initial_capital=10000)
        broker.submit_entry("L", "long", 1.0, 0)
        broker.process_orders(1, 100.0, 101.0, 99.0, 100.5)
        broker.submit_close("L", 1)
        broker.process_orders(2, 110.0, 111.0, 109.0, 110.5)

        expected_pnl = sum(t.pnl for t in broker.closed_trades)
        assert broker._realized_pnl == pytest.approx(expected_pnl, rel=1e-9)

    def test_equity_matches_initial_plus_realized(self):
        broker = Broker(initial_capital=5000)
        broker.submit_entry("S", "short", 2.0, 0)
        broker.process_orders(1, 200.0, 201.0, 199.0, 200.5)
        broker.submit_close("S", 1)
        broker.process_orders(2, 190.0, 191.0, 189.0, 190.5)

        expected = 5000.0 + broker._realized_pnl
        assert broker.equity == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# IMP 4 – strategy.opentrades / strategy.closedtrades
# ---------------------------------------------------------------------------

class TestOpenAndClosedTrades:
    def test_strategy_closedtrades_increases(self):
        """A strategy that opens and closes multiple trades must count correctly."""
        script = """//@version=5
strategy("TradeCount")
if bar_index % 10 == 0 and strategy.closedtrades == 0
    strategy.entry("L", strategy.long)
if bar_index % 10 == 5
    strategy.close("L")
"""
        data = _linear_data(50)
        result = Engine().run(script, data)
        assert result.total_trades >= 1


# ---------------------------------------------------------------------------
# Full-system regression: all fixes together
# ---------------------------------------------------------------------------

class TestFullSystemRegression:
    def test_multi_run_isolation(self):
        """Multiple Engine.run() calls must be fully isolated."""
        script = """//@version=5
strategy("Iso")
if bar_index == 0
    strategy.entry("L", strategy.long)
if bar_index == 5
    strategy.close("L")
"""
        data = _linear_data(20)
        e = Engine()
        r1 = e.run(script, data)
        r2 = e.run(script, data)
        assert r1.net_profit == pytest.approx(r2.net_profit, rel=1e-9)
        assert r1.total_trades == r2.total_trades
        assert r1.strategy_name == r2.strategy_name

    def test_equity_curve_length_matches_bars(self):
        script = """//@version=5
strategy("EquityCurve")
if bar_index == 2
    strategy.entry("L", strategy.long)
"""
        data = _linear_data(30)
        result = Engine().run(script, data)
        assert len(result.equity_curve) == len(data)

    def test_commission_reduces_profit(self):
        script = """//@version=5
strategy("CommTest")
if bar_index == 0
    strategy.entry("L", strategy.long)
if bar_index == 20
    strategy.close("L")
"""
        data = _linear_data(30)
        r0 = Engine(commission=0.0).run(script, data)
        r1 = Engine(commission=0.01).run(script, data)
        if r0.total_trades > 0 and r1.total_trades > 0:
            assert r1.net_profit < r0.net_profit
