"""CLI entry point: python -m strateg run --script strategy.pine --data ohlcv.csv"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="strateg",
        description="Pine Script v5 Strategy Backtester Engine",
    )
    sub = parser.add_subparsers(dest="command")

    # --- run ---
    run_p = sub.add_parser("run", help="Run a backtest")
    run_p.add_argument("--script", "-s", required=True, help="Path to .pine script")
    data_grp = run_p.add_mutually_exclusive_group(required=True)
    data_grp.add_argument("--data", "-d", help="Path to OHLCV CSV file")
    data_grp.add_argument("--symbol", help="Download data by symbol (e.g. XAUUSD, AAPL, BTC-USD)")
    run_p.add_argument("--start", default="2020-01-01", help="Start date for --symbol download (default: 2020-01-01)")
    run_p.add_argument("--end", default=None, help="End date for --symbol download (default: today)")
    run_p.add_argument("--interval", default="1d", help="Bar interval for --symbol (1m,5m,15m,1h,1d,1wk)")
    run_p.add_argument("--capital", "-c", type=float, default=10000.0, help="Initial capital")
    run_p.add_argument("--commission", type=float, default=0.0, help="Commission rate (e.g. 0.001 for 0.1%%)")
    run_p.add_argument("--slippage", type=float, default=0.0, help="Slippage per trade")
    run_p.add_argument("--fill-on", choices=["next_open", "close"], default="next_open", help="Order fill timing")
    run_p.add_argument("--trades", action="store_true", help="Print trade log")

    # --- download ---
    dl_p = sub.add_parser("download", help="Download OHLCV data to CSV")
    dl_p.add_argument("symbol", help="Ticker or alias (e.g. XAUUSD, GC=F, AAPL, BTC-USD)")
    dl_p.add_argument("--start", default="2020-01-01", help="Start date (default: 2020-01-01)")
    dl_p.add_argument("--end", default=None, help="End date (default: today)")
    dl_p.add_argument("--interval", "-i", default="1d", help="Bar interval (1m,5m,15m,1h,1d,1wk)")
    dl_p.add_argument("--output", "-o", default=None, help="Output CSV path (default: <symbol>_<interval>.csv)")

    # --- live ---
    live_p = sub.add_parser("live", help="Run strategy live against Exness MT5 via MetaAPI")
    live_p.add_argument("--script", "-s", required=True, help="Path to .pine script")
    live_p.add_argument("--symbol", default="XAUUSDm", help="MT5 symbol (default: XAUUSDm)")
    live_p.add_argument("--timeframe", "-t", default="1h",
                        help="Candle timeframe: 1m,5m,15m,30m,1h,4h,1d (default: 1h)")
    live_p.add_argument("--lot", type=float, default=0.01, help="Lot size per trade (default: 0.01)")
    live_p.add_argument("--max-lot", type=float, default=0.1, help="Maximum lot size (default: 0.1)")
    live_p.add_argument("--max-daily-loss", type=float, default=5.0,
                        help="Max daily loss %% before halting (default: 5.0)")
    live_p.add_argument("--max-positions", type=int, default=1, help="Max simultaneous open positions (default: 1)")
    live_p.add_argument("--cooldown", type=int, default=60, help="Seconds between trades (default: 60)")
    live_p.add_argument("--poll", type=int, default=60, help="Poll interval in seconds (default: 60)")
    live_p.add_argument("--lookback", type=int, default=200, help="Historical bars for warmup (default: 200)")
    live_p.add_argument("--live", action="store_true", dest="is_live",
                        help="ENABLE REAL TRADING. Without this flag, runs in dry-run mode.")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        _run_backtest(args)
    elif args.command == "download":
        _download_data(args)
    elif args.command == "live":
        _run_live(args)


def _run_backtest(args: argparse.Namespace) -> None:
    from .engine import Engine
    from .data import load_csv, download

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: Script file not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    if args.symbol:
        data = download(
            symbol=args.symbol,
            start=args.start,
            end=args.end,
            interval=args.interval,
        )
        data_label = f"{args.symbol} ({args.interval})"
    else:
        data_path = Path(args.data)
        if not data_path.exists():
            print(f"Error: Data file not found: {data_path}", file=sys.stderr)
            sys.exit(1)
        data = load_csv(data_path)
        data_label = data_path.name

    source = script_path.read_text()

    engine = Engine(
        initial_capital=args.capital,
        commission=args.commission,
        slippage=args.slippage,
        fill_on=args.fill_on,
    )

    print(f"Loading strategy: {script_path.name}")
    print(f"Data: {data_label} ({len(data)} bars)")
    print()

    result = engine.run(source, data)
    print(result.summary())

    if args.trades:
        print()
        print(result.trade_log())


def _download_data(args: argparse.Namespace) -> None:
    from .data import download, resolve_symbol

    output = args.output
    if not output:
        safe_name = resolve_symbol(args.symbol).replace("=", "").replace("^", "").replace("/", "")
        output = f"{safe_name}_{args.interval}.csv"

    download(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        interval=args.interval,
        output=output,
    )


def _run_live(args: argparse.Namespace) -> None:
    import asyncio
    import logging
    from .live.config import load_config
    from .live.bridge import run_live

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler("strateg_live.log"),
            logging.StreamHandler(sys.stderr),
        ],
    )

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: Script file not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config(
        script_path=str(script_path),
        symbol=args.symbol,
        timeframe=args.timeframe,
        lot_size=args.lot,
        max_lot_size=args.max_lot,
        max_daily_loss_pct=args.max_daily_loss,
        max_open_positions=args.max_positions,
        cooldown_seconds=args.cooldown,
        poll_interval_seconds=args.poll,
        lookback_bars=args.lookback,
        is_live=args.is_live,
    )

    errors = config.validate()
    if errors:
        for e in errors:
            print(f"Config error: {e}", file=sys.stderr)
        print("\nMake sure you have a .env file with METAAPI_TOKEN and METAAPI_ACCOUNT_ID.", file=sys.stderr)
        print("See .env.example for the template.", file=sys.stderr)
        sys.exit(1)

    if config.is_live:
        print("\n" + "!" * 60)
        print("  WARNING: LIVE TRADING MODE ENABLED")
        print("  Real orders WILL be placed on your Exness account.")
        print("!" * 60 + "\n")

    asyncio.run(run_live(config))


if __name__ == "__main__":
    main()
