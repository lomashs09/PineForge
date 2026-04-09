# PineForge

A Python engine that lexes, parses, and interprets **Pine Script v5** pineforgey files (`.pine`), executes them bar-by-bar against OHLCV data with a simulated broker, and produces full backtest results — trade logs, equity curves, and performance metrics. It also includes a **live trading bridge** for executing pineforgeies on a real MetaTrader 5 account via MetaAPI Cloud.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Installation](#installation)
- [Data Acquisition](#data-acquisition)
  - [Download via CLI](#download-via-cli)
  - [Use Your Own CSV](#use-your-own-csv)
  - [Supported Symbols](#supported-symbols)
- [Running a Backtest](#running-a-backtest)
  - [Using a CSV File](#using-a-csv-file)
  - [Using a Symbol (Auto-Download)](#using-a-symbol-auto-download)
  - [Backtest Options](#backtest-options)
- [Live Trading](#live-trading)
  - [MetaAPI Setup](#metaapi-setup)
  - [Environment Variables](#environment-variables)
  - [Running in Dry-Run Mode](#running-in-dry-run-mode)
  - [Running Live](#running-live)
  - [Live Trading Options](#live-trading-options)
  - [Risk Management](#risk-management)
- [Writing a Pine Script PineForgey](#writing-a-pine-script-pineforgey)
  - [Minimal Example](#minimal-example)
  - [Using Inputs and Indicators](#using-inputs-and-indicators)
- [Supported Pine Script Features](#supported-pine-script-features)
  - [Language](#language)
  - [Built-in Variables](#built-in-variables)
  - [Technical Analysis (ta.*)](#technical-analysis-ta)
  - [Math (math.*)](#math-math)
  - [Input (input.*)](#input-input)
  - [PineForgey (pineforgey.*)](#pineforgey-pineforgey)
- [Example PineForgeies](#example-pineforgeies)
- [Backtest Output & Metrics](#backtest-output--metrics)
- [CSV Format](#csv-format)
- [Project Structure](#project-structure)
- [Tests](#tests)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Pine Script v5 compiler pipeline** — Lexer, Parser (recursive descent), AST, tree-walking Interpreter
- **Bar-by-bar execution** — mirrors how TradingView evaluates pineforgeies
- **Simulated broker** — market orders, entry/exit logic, position tracking, commission & slippage
- **Series type** — every value is a time series with `[n]` history access, just like Pine
- **14 built-in TA indicators** — SMA, EMA, RMA, RSI, MACD, Crossover/Under, Highest, Lowest, ATR, StdDev, and more
- **Data download** — fetch OHLCV data from Yahoo Finance for any supported ticker
- **Performance metrics** — win rate, profit factor, Sharpe ratio, max drawdown, equity curve
- **Live trading bridge** — execute pineforgeies on MetaTrader 5 (Exness, etc.) via MetaAPI Cloud
- **Risk manager** — daily loss limits, max positions, trade cooldown, position sizing
- **13 example pineforgeies** — from simple crossovers to multi-factor scoring and regime-adaptive systems

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     .pine Source File                     │
└────────────────────────────┬─────────────────────────────┘
                             │
                   ┌─────────▼─────────┐
                   │      Lexer        │  tokens.py, lexer.py
                   │  (Tokenization)   │
                   └─────────┬─────────┘
                             │ Token stream
                   ┌─────────▼─────────┐
                   │      Parser       │  parser.py, ast_nodes.py
                   │ (Recursive Descent)│
                   └─────────┬─────────┘
                             │ AST
                   ┌─────────▼─────────┐
                   │   Interpreter     │  interpreter.py, series.py
                   │  (Tree-Walking)   │  environment.py
                   └─────────┬─────────┘
                             │ pineforgey.entry / pineforgey.close calls
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼────┐  ┌─────▼──────┐  ┌───▼──────────┐
    │   Broker     │  │  Built-ins │  │   Engine     │
    │ (Simulated)  │  │ ta, math,  │  │ (Backtest    │
    │ Orders, PnL  │  │ input,     │  │  Orchestrator)│
    └──────────────┘  │ pineforgey   │  └───┬──────────┘
                      └────────────┘      │
                                   ┌──────▼──────┐
                                   │   Results   │  results.py
                                   │  Metrics,   │
                                   │  Trade Log  │
                                   └─────────────┘

Live Trading Path:

    .pine ──▶ Interpreter ──▶ Signal Detection ──▶ Risk Manager ──▶ Executor ──▶ MetaAPI ──▶ MT5
                                                                     │
                                                               executor.py
                                                               bridge.py
                                                               feed.py
                                                               risk.py
                                                               config.py
```

### Key modules

| Module | File(s) | Purpose |
|--------|---------|---------|
| Lexer | `pineforge/tokens.py`, `pineforge/lexer.py` | Tokenizes Pine Script source into a token stream |
| Parser | `pineforge/parser.py`, `pineforge/ast_nodes.py` | Recursive descent parser producing an AST |
| Interpreter | `pineforge/interpreter.py` | Tree-walking interpreter, executes AST per bar |
| Series | `pineforge/series.py` | Time-indexed series with `[n]` history access |
| Environment | `pineforge/environment.py` | Scoped symbol table, `var` persistence across bars |
| Broker | `pineforge/broker.py` | Simulated order execution, position tracking, PnL |
| Engine | `pineforge/engine.py` | Orchestrates the full backtest pipeline |
| Data | `pineforge/data.py` | CSV loading, Yahoo Finance download, symbol aliases |
| Results | `pineforge/results.py` | Computes all performance metrics from trade log |
| Built-ins | `pineforge/builtins/` | `ta.*`, `math.*`, `input.*`, `pineforgey.*` functions |
| Live Bridge | `pineforge/live/bridge.py` | Main live trading loop — poll, interpret, execute |
| Live Executor | `pineforge/live/executor.py` | Places/closes orders via MetaAPI RPC |
| Live Feed | `pineforge/live/feed.py` | Fetches candles from MetaAPI |
| Live Risk | `pineforge/live/risk.py` | Position sizing, daily loss limits, cooldowns |
| Live Config | `pineforge/live/config.py` | Loads trading parameters from `.env` and CLI args |

---

## Installation

```bash
git clone https://github.com/your-username/pineforge.git
cd pineforge

# Install in editable mode
python3 -m pip install -e .

# Or install dependencies directly
python3 -m pip install -r requirements.txt
```

**Requirements:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pandas` | >= 2.0 | DataFrame operations, CSV handling |
| `yfinance` | >= 0.2 | Yahoo Finance data download |
| `metaapi-cloud-sdk` | >= 29.0 | MetaTrader 5 API for live trading |
| `python-dotenv` | >= 1.0 | Load `.env` files |

**Python version:** 3.9+ (3.11+ recommended)

---

## Data Acquisition

### Download via CLI

Use the `download` subcommand to fetch OHLCV data from Yahoo Finance:

```bash
# Daily gold data from 2020 to today
python3 -m pineforge download XAUUSD --start 2020-01-01 -o examples/xauusd_daily.csv

# 1-hour gold data (limited to ~730 days back by Yahoo)
python3 -m pineforge download XAUUSD --interval 1h --start 2024-06-01 -o examples/xauusd_1h.csv

# 15-minute data (limited to ~60 days back)
python3 -m pineforge download XAUUSD --interval 15m -o examples/xauusd_15m.csv

# Apple stock, weekly bars
python3 -m pineforge download AAPL --interval 1wk --start 2015-01-01 -o examples/aapl_weekly.csv

# Bitcoin daily
python3 -m pineforge download BTC-USD --start 2020-01-01 -o examples/btc_daily.csv
```

**Download options:**

| Flag | Default | Description |
|------|---------|-------------|
| `symbol` (positional) | — | Ticker or alias (see below) |
| `--start` | `2020-01-01` | Start date (YYYY-MM-DD) |
| `--end` | today | End date |
| `--interval`, `-i` | `1d` | Interval: `1m`, `5m`, `15m`, `1h`, `1d`, `1wk` |
| `--output`, `-o` | `<symbol>_<interval>.csv` | Output file path |

> **Note:** Yahoo Finance limits historical intraday data. 1h data goes back ~730 days, 15m goes back ~60 days, and 1m goes back ~7 days.

### Use Your Own CSV

Provide any CSV with OHLCV columns. The loader auto-detects common column names:

```csv
date,open,high,low,close,volume
2024-01-02,2060.50,2075.30,2055.10,2070.20,150000
2024-01-03,2070.20,2085.40,2068.00,2080.10,142000
```

Accepted column name variations:
- Date: `date`, `Date`, `datetime`, `Datetime`, `time`, `timestamp`
- OHLCV: `open`/`Open`, `high`/`High`, `low`/`Low`, `close`/`Close`, `volume`/`Volume`

### Supported Symbols

The `download` command maps common aliases to Yahoo Finance tickers:

| Alias | Yahoo Ticker | Asset |
|-------|-------------|-------|
| `XAUUSD` | `GC=F` | Gold |
| `XAGUSD` | `SI=F` | Silver |
| `BTCUSD` | `BTC-USD` | Bitcoin |
| `ETHUSD` | `ETH-USD` | Ethereum |
| `EURUSD` | `EURUSD=X` | EUR/USD |
| `GBPUSD` | `GBPUSD=X` | GBP/USD |
| `USDJPY` | `JPY=X` | USD/JPY |
| `SPX` | `^GSPC` | S&P 500 |
| `NASDAQ` | `^IXIC` | Nasdaq Composite |
| `DJI` | `^DJI` | Dow Jones |
| `OIL` | `CL=F` | Crude Oil |

Any ticker not in this list is passed directly to Yahoo Finance (e.g., `AAPL`, `TSLA`, `MSFT`).

---

## Running a Backtest

### Using a CSV File

```bash
python3 -m pineforge run --script examples/sma_crossover.pine --data examples/xauusd_daily.csv
```

### Using a Symbol (Auto-Download)

Skip the CSV step — download and backtest in one command:

```bash
python3 -m pineforge run --script examples/ema_rsi_trend.pine --symbol XAUUSD --interval 1h --start 2024-06-01
```

### Print Trade Log

Add `--trades` to see every trade:

```bash
python3 -m pineforge run --script examples/rsi_mean_reversion.pine --data examples/xauusd_1h.csv --trades
```

### Backtest Options

| Flag | Default | Description |
|------|---------|-------------|
| `--script`, `-s` | — | **(required)** Path to `.pine` file |
| `--data`, `-d` | — | Path to OHLCV CSV file |
| `--symbol` | — | Download data by symbol (alternative to `--data`) |
| `--start` | `2020-01-01` | Start date (when using `--symbol`) |
| `--end` | today | End date (when using `--symbol`) |
| `--interval` | `1d` | Bar interval (when using `--symbol`) |
| `--capital`, `-c` | `10000.0` | Initial capital ($) |
| `--commission` | `0.0` | Commission per trade (e.g. `0.001` for 0.1%) |
| `--slippage` | `0.0` | Slippage per fill (absolute price units) |
| `--fill-on` | `next_open` | When to fill orders: `next_open` or `close` |
| `--trades` | off | Print full trade log |

---

## Live Trading

The live module connects to MetaTrader 5 via [MetaAPI Cloud](https://metaapi.cloud/) and runs your Pine pineforgey in real-time against incoming candles.

### MetaAPI Setup

1. Create an account at [metaapi.cloud](https://app.metaapi.cloud/)
2. Add your MT5 account (broker server, account number, investor/trading password)
3. Wait for the account to reach **DEPLOYED** + **CONNECTED** status
4. Copy your **API Token** and **Account ID** from the MetaAPI dashboard

### Environment Variables

Create a `.env` file in the project root:

```env
METAAPI_TOKEN=your-metaapi-token-here
METAAPI_ACCOUNT_ID=your-mt5-account-id-here
```

A template is provided in `.env.example`.

### Running in Dry-Run Mode

By default, the bridge runs in **dry-run mode** — it connects, fetches data, runs the pineforgey, and logs what trades it *would* place, but does not touch your account:

```bash
python3 -m pineforge live --script examples/ema_rsi_trend.pine --symbol XAUUSDm --timeframe 1h --lot 0.01
```

### Running Live

Add the `--live` flag to enable real order execution:

```bash
python3 -m pineforge live --script examples/ema_rsi_trend.pine --symbol XAUUSDm --timeframe 1h --lot 0.01 --live
```

> **Warning:** This places real orders on your trading account. Start with a demo account.

### Live Trading Options

| Flag | Default | Description |
|------|---------|-------------|
| `--script`, `-s` | — | **(required)** Path to `.pine` file |
| `--symbol` | `XAUUSDm` | MT5 symbol name (must match your broker) |
| `--timeframe`, `-t` | `1h` | Candle timeframe: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d` |
| `--lot` | `0.01` | Lot size per trade |
| `--max-lot` | `0.1` | Maximum lot size |
| `--max-daily-loss` | `5.0` | Max daily loss as % of balance before auto-halt |
| `--max-positions` | `1` | Max simultaneous open positions |
| `--cooldown` | `60` | Minimum seconds between trades |
| `--poll` | `60` | Poll interval in seconds |
| `--lookback` | `200` | Number of historical bars for pineforgey warmup |
| `--live` | off | Enable real order execution (default is dry-run) |

### Risk Management

The live bridge includes an automatic risk manager that enforces:

- **Max daily loss** — if cumulative realized + unrealized loss exceeds the threshold (default 5%), all trading halts for the day
- **Max open positions** — prevents opening more positions than allowed (default 1)
- **Trade cooldown** — minimum time between consecutive trades (default 60 seconds)
- **Position sizing** — respects `--lot` and `--max-lot` bounds

---

## Writing a Pine Script PineForgey

### Minimal Example

```pinescript
//@version=5
pineforgey("My PineForgey", overlay=true)

fast = ta.sma(close, 10)
slow = ta.sma(close, 30)

if ta.crossover(fast, slow)
    pineforgey.entry("Long", pineforgey.long)

if ta.crossunder(fast, slow)
    pineforgey.close("Long")
```

### Using Inputs and Indicators

```pinescript
//@version=5
pineforgey("EMA Trend + RSI Filter", overlay=true)

ema_len = input.int(50, "EMA Length")
rsi_len = input.int(14, "RSI Length")
rsi_ob  = input.int(70, "RSI Overbought")
rsi_os  = input.int(30, "RSI Oversold")

ema = ta.ema(close, ema_len)
rsi = ta.rsi(close, rsi_len)

if ta.crossover(close, ema) and rsi < rsi_ob
    pineforgey.entry("Long", pineforgey.long)

if ta.crossunder(close, ema) or rsi > rsi_ob
    pineforgey.close("Long")
```

---

## Supported Pine Script Features

### Language

| Feature | Details |
|---------|---------|
| Version directive | `//@version=5` |
| Variables | `x = expr`, `var x = expr` (persists across bars), `x := expr` |
| Augmented assignment | `+=`, `-=`, `*=`, `/=`, `%=` |
| Control flow | `if` / `else if` / `else`, `for x = a to b (by step)`, `for x in collection`, `while` |
| Operators | `+`, `-`, `*`, `/`, `%`, `==`, `!=`, `<`, `>`, `<=`, `>=`, `and`, `or`, `not` |
| Ternary | `condition ? true_val : false_val` |
| History reference | `close[1]`, `ta.sma(close, 14)[3]` |
| User-defined functions | `f(x, y) => expr` |
| Literals | integers, floats, strings, `true`, `false`, `na` |
| Named arguments | `input.int(14, title="Length")` |

### Built-in Variables

| Variable | Description |
|----------|-------------|
| `open`, `high`, `low`, `close`, `volume` | Current bar OHLCV |
| `hl2` | `(high + low) / 2` |
| `hlc3` | `(high + low + close) / 3` |
| `ohlc4` | `(open + high + low + close) / 4` |
| `bar_index` | Current bar number (0-based) |

### Technical Analysis (`ta.*`)

| Function | Signature | Description |
|----------|-----------|-------------|
| `ta.sma` | `ta.sma(source, length)` | Simple Moving Average |
| `ta.ema` | `ta.ema(source, length)` | Exponential Moving Average |
| `ta.rma` | `ta.rma(source, length)` | Running (Wilder's) Moving Average |
| `ta.rsi` | `ta.rsi(source, length)` | Relative Strength Index |
| `ta.macd` | `ta.macd(source, fast, slow, signal)` | MACD (returns tuple) |
| `ta.crossover` | `ta.crossover(a, b)` | `true` when `a` crosses above `b` |
| `ta.crossunder` | `ta.crossunder(a, b)` | `true` when `a` crosses below `b` |
| `ta.highest` | `ta.highest(source, length)` | Highest value over lookback |
| `ta.lowest` | `ta.lowest(source, length)` | Lowest value over lookback |
| `ta.change` | `ta.change(source, length?)` | Change from `length` bars ago |
| `ta.stdev` | `ta.stdev(source, length)` | Standard deviation |
| `ta.tr` | `ta.tr(handle_na?)` | True range |
| `ta.atr` | `ta.atr(length)` | Average True Range (via `ta.rma(ta.tr, length)`) |

### Math (`math.*`)

| Function | Description |
|----------|-------------|
| `math.abs(x)` | Absolute value |
| `math.max(a, b)` | Maximum |
| `math.min(a, b)` | Minimum |
| `math.round(x)` | Round to nearest integer |
| `math.ceil(x)` | Ceiling |
| `math.floor(x)` | Floor |
| `math.log(x)` | Natural logarithm |
| `math.log10(x)` | Base-10 logarithm |
| `math.sqrt(x)` | Square root |
| `math.pow(base, exp)` | Power |
| `math.sign(x)` | Sign (-1, 0, 1) |
| `math.avg(a, b, ...)` | Average of arguments |
| `math.sum(source, length)` | Rolling sum |
| `nz(x, replacement?)` | Replace `na` with 0 (or custom value) |
| `na(x)` | Check if value is `na` |
| `fixnan(x)` | Replace `na` with last non-na value |

### Input (`input.*`)

| Function | Description |
|----------|-------------|
| `input(defval, title?)` | Generic input |
| `input.int(defval, title?)` | Integer input |
| `input.float(defval, title?)` | Float input |
| `input.bool(defval, title?)` | Boolean input |
| `input.string(defval, title?)` | String input |
| `input.source(defval, title?)` | Source series input |

### PineForgey (`pineforgey.*`)

| Function | Description |
|----------|-------------|
| `pineforgey(title, overlay?, ...)` | Declare pineforgey properties |
| `pineforgey.entry(id, direction)` | Open a position (market order) |
| `pineforgey.close(id)` | Close a position by entry ID |
| `pineforgey.close_all()` | Close all open positions |
| `pineforgey.exit(id, from_entry?, stop?, limit?)` | Set stop-loss / take-profit |
| `pineforgey.order(id, direction)` | Place a generic order |
| `pineforgey.long` | Long direction constant |
| `pineforgey.short` | Short direction constant |

---

## Example PineForgeies

The `examples/` directory contains 13 ready-to-run pineforgeies:

| File | PineForgey | Description |
|------|----------|-------------|
| `sma_crossover.pine` | SMA Crossover | Classic fast/slow SMA crossover |
| `ema_crossover.pine` | EMA Crossover 9/21 | EMA 9 vs EMA 21 crossover |
| `triple_ema.pine` | Triple EMA | Three-EMA ribbon (8/21/55) |
| `rsi_mean_reversion.pine` | RSI Mean Reversion | Buy oversold, sell overbought |
| `bollinger_bands.pine` | Bollinger Band Reversion | Fade moves to BB extremes |
| `breakout.pine` | Donchian Breakout | Breakout of N-bar high/low channel |
| `ema_rsi_trend.pine` | EMA Trend + RSI Filter | Trend-following with RSI confirmation |
| `momentum.pine` | Momentum ROC | Rate-of-change momentum with SMA filter |
| `atr_trend_follow.pine` | ATR Trend Follow | EMA crossover with ATR-based trailing stop |
| `multi_factor.pine` | Multi-Factor Scoring | Scores trend, momentum, and volatility |
| `squeeze_breakout.pine` | Volatility Squeeze | BB inside Keltner Channel squeeze detection |
| `pullback_buyer.pine` | Trend Pullback Buyer | Buys pullbacks in established trends |
| `adaptive_channel.pine` | Adaptive Channel Breakout | Dynamic channel width based on volatility |
| `regime_switch.pine` | Regime-Adaptive | Switches between trend and mean-reversion modes |
| `test_orders.pine` | Order Test | Simple test script for live order verification |

Run any of them:

```bash
# Backtest with auto-download
python3 -m pineforge run -s examples/bollinger_bands.pine --symbol XAUUSD --interval 1h --start 2024-06-01 --trades

# Backtest with CSV
python3 -m pineforge run -s examples/atr_trend_follow.pine -d examples/xauusd_1h.csv --capital 5000

# Live dry-run
python3 -m pineforge live -s examples/ema_rsi_trend.pine --symbol XAUUSDm --timeframe 1h
```

---

## Backtest Output & Metrics

A backtest prints a summary like this:

```
═══════════════════════════════════════
  PineForgey: EMA Trend + RSI Filter
═══════════════════════════════════════
  Initial Capital:    $10,000.00
  Final Equity:       $11,452.30
  Net Profit:         $1,452.30  (14.52%)
  Total Trades:       47
  Win Rate:           55.32%
  Profit Factor:      1.83
  Max Drawdown:       $620.40  (5.41%)
  Sharpe Ratio:       1.24
  Avg Trade PnL:      $30.90
  Avg Win:            $85.20
  Avg Loss:           -$36.10
═══════════════════════════════════════
```

All computed metrics:

| Metric | Description |
|--------|-------------|
| `initial_capital` | Starting balance |
| `final_equity` | Ending balance |
| `net_profit` | Total profit/loss in dollars |
| `total_return_pct` | Net profit as % of initial capital |
| `total_trades` | Number of completed (closed) trades |
| `winning_trades` | Trades with positive PnL |
| `losing_trades` | Trades with zero or negative PnL |
| `win_rate` | Percentage of winning trades |
| `gross_profit` | Sum of all winning trade PnLs |
| `gross_loss` | Sum of all losing trade PnLs |
| `profit_factor` | `gross_profit / abs(gross_loss)` |
| `max_drawdown` | Largest peak-to-trough decline ($) |
| `max_drawdown_pct` | Max drawdown as % of peak equity |
| `sharpe_ratio` | Annualized Sharpe ratio (252-day) |
| `avg_trade_pnl` | Mean PnL per trade |
| `avg_winning_trade` | Mean PnL of winning trades |
| `avg_losing_trade` | Mean PnL of losing trades |
| `equity_curve` | List of equity values per bar |
| `trades` | Full list of `Trade` objects with entry/exit details |

---

## CSV Format

The engine accepts CSV files with these columns (case-insensitive, auto-detected):

```csv
date,open,high,low,close,volume
2024-01-02,2060.50,2075.30,2055.10,2070.20,150000
2024-01-03,2070.20,2085.40,2068.00,2080.10,142000
```

- **date** column is optional (the engine works fine without it)
- **volume** column is optional (defaults to 0 if missing)
- Column names are matched flexibly: `Date`/`date`/`datetime`/`Datetime`/`time`/`timestamp` all work for the date column

---

## Project Structure

```
pineforge/
├── __init__.py           # Package init, version
├── __main__.py           # CLI entry point (run, download, live)
├── tokens.py             # TokenType enum, Token dataclass
├── lexer.py              # Tokenizer for Pine Script v5
├── ast_nodes.py          # AST node dataclasses (22 node types)
├── parser.py             # Recursive descent parser → AST
├── interpreter.py        # Tree-walking interpreter (bar-by-bar)
├── series.py             # Series class with [n] history access
├── environment.py        # Scoped symbol table (var persistence)
├── broker.py             # Simulated broker (orders, positions, PnL)
├── engine.py             # Backtest orchestrator
├── data.py               # CSV loader, yfinance downloader, symbol map
├── results.py            # Metric computation from trade log
├── builtins/
│   ├── __init__.py
│   ├── ta.py             # ta.sma, ta.ema, ta.rsi, etc.
│   ├── math_funcs.py     # math.abs, nz, na, etc.
│   ├── input_funcs.py    # input.int, input.float, etc.
│   └── pineforgey.py       # pineforgey.entry, pineforgey.close, etc.
├── live/
│   ├── __init__.py
│   ├── config.py         # LiveConfig from .env + CLI args
│   ├── bridge.py         # Main live trading loop
│   ├── executor.py       # MetaAPI order execution
│   ├── feed.py           # Candle fetching from MetaAPI
│   └── risk.py           # Risk manager (daily loss, cooldown, sizing)
examples/
├── *.pine                # 15 example pineforgey scripts
├── *.csv                 # Sample data files
├── run_backtest.py       # Python-based backtest runner
tests/
├── test_lexer.py         # Lexer unit tests
├── test_parser.py        # Parser unit tests
├── test_interpreter.py   # Interpreter unit tests
└── test_engine.py        # Full engine integration tests
```

---

## Tests

Run the test suite with pytest:

```bash
python3 -m pytest tests/ -v
```

Tests cover:
- **Lexer** — tokenization of numbers, strings, operators, keywords, indentation
- **Parser** — AST generation for assignments, control flow, expressions, function defs
- **Interpreter** — evaluation of expressions, variables, `var` persistence, `if`/`for`, user-defined functions
- **Engine** — end-to-end backtests verifying trade generation, equity curve length, profitability on trending data

---

## Deployment

To run the live bridge 24/7, deploy to a cloud VM. Recommended free options:

**Oracle Cloud Free Tier** (best — always free, 4 CPU / 24 GB RAM ARM VM):

```bash
ssh ubuntu@<your-vm-ip>
sudo apt update && sudo apt install -y python3 python3-pip git
git clone <your-repo> pineforge && cd pineforge
pip3 install -e .
cp .env.example .env  # edit with your MetaAPI credentials
```

Run persistently with systemd:

```ini
# /etc/systemd/system/pineforge.service
[Unit]
Description=PineForge Live Trader
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/pineforge
ExecStart=/usr/bin/python3 -m pineforge live -s examples/ema_rsi_trend.pine --symbol XAUUSDm --timeframe 1h --lot 0.01 --live
Restart=always
RestartSec=30
EnvironmentFile=/home/ubuntu/pineforge/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable pineforge && sudo systemctl start pineforge
```

Other free options: **Google Cloud e2-micro** (always free), **AWS t2.micro** (12 months free), **Fly.io** (3 free VMs), or just a **Raspberry Pi** at home.

---

## Contributing

1. Fork the repo and create a feature branch
2. Follow the existing code style — no unnecessary comments, type hints preferred
3. Add tests for new features in `tests/`
4. Run `python3 -m pytest tests/ -v` and make sure everything passes
5. Open a pull request with a clear description of what you changed and why

### Areas that could use contributions

- More `ta.*` indicators (Stochastic, Williams %R, Ichimoku, Pivot Points)
- `pineforgey.exit` with trailing stop support
- Multi-timeframe support (`request.security`)
- Plotting / visualization of equity curves and indicators
- `alert()` function with webhook/Telegram notifications
- Persistent state across bridge restarts (save/load interpreter state)
- Web UI dashboard for monitoring live trades

---

## License

MIT
