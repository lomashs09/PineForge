"""
Fetch trade history from MetaAPI for a date range and print a summary.

Uses .env: METAAPI_TOKEN, METAAPI_ACCOUNT_ID.

Usage:
  python scripts/fetch_trade_history.py

  # Custom range (ISO dates, UTC):
  python scripts/fetch_trade_history.py --start 2026-03-10 --end 2026-03-13

REST endpoints:
  GET .../history-orders/time/:startTime/:endTime
  GET .../history-deals/time/:startTime/:endTime  (for P&L)
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()

# Default: new-york region. Other regions: https://app.metaapi.cloud/token
METAAPI_BASE = os.getenv("METAAPI_REST_URL", "https://mt-client-api-v1.new-york.agiliumtrade.ai")


def fetch_json(token: str, path: str, account_id: str, start_iso: str, end_iso: str) -> list:
    url = f"{METAAPI_BASE}/users/current/accounts/{account_id}{path}/{start_iso}/{end_iso}"
    req = Request(url, headers={"Accept": "application/json", "auth-token": token})
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise SystemExit(f"HTTP {e.code}: {body}")
    except URLError as e:
        raise SystemExit(f"Request failed: {e.reason}")


def main():
    parser = argparse.ArgumentParser(description="Fetch MetaAPI trade history and show results")
    parser.add_argument("--start", default="2026-03-10", help="Start date YYYY-MM-DD (UTC)")
    parser.add_argument("--end", default="2026-03-13", help="End date YYYY-MM-DD (exclusive, UTC)")
    args = parser.parse_args()

    token = os.getenv("METAAPI_TOKEN")
    account_id = os.getenv("METAAPI_ACCOUNT_ID")
    if not token or not account_id:
        print("ERROR: Set METAAPI_TOKEN and METAAPI_ACCOUNT_ID in .env", file=sys.stderr)
        sys.exit(1)

    start_iso = f"{args.start}T00:00:00.000Z"
    end_iso = f"{args.end}T00:00:00.000Z"
    print(f"Fetching history for {args.start} to {args.end} (exclusive end)...")
    print(f"Account: {account_id}")
    print()

    orders = fetch_json(token, "/history-orders/time", account_id, start_iso, end_iso)
    deals = fetch_json(token, "/history-deals/time", account_id, start_iso, end_iso)

    # Filter to trading deals (open/close), not balance/commission-only
    trade_deal_types = {"DEAL_TYPE_BUY", "DEAL_TYPE_SELL", "DEAL_TYPE_BUY_CANCELED", "DEAL_TYPE_SELL_CANCELED"}
    trading_deals = [d for d in deals if d.get("type") in trade_deal_types]

    # Orders summary
    filled = [o for o in orders if o.get("state") == "ORDER_STATE_FILLED"]
    by_symbol = {}
    for o in filled:
        s = o.get("symbol", "?")
        by_symbol[s] = by_symbol.get(s, 0) + 1

    print("=== HISTORY ORDERS (filled) ===")
    print(f"Total filled orders: {len(filled)}")
    if by_symbol:
        for sym, count in sorted(by_symbol.items(), key=lambda x: -x[1]):
            print(f"  {sym}: {count}")
    print()

    # Deals summary and P&L
    total_profit = sum(d.get("profit") or 0 for d in trading_deals)
    total_commission = sum(d.get("commission") or 0 for d in deals)
    total_swap = sum(d.get("swap") or 0 for d in deals)
    net = total_profit + total_commission + total_swap

    print("=== HISTORY DEALS (trading) ===")
    print(f"Trading deals (open/close): {len(trading_deals)}")
    print(f"Total profit (from deals):  {total_profit:.2f}")
    print(f"Total commission:           {total_commission:.2f}")
    print(f"Total swap:                 {total_swap:.2f}")
    print(f"Net (profit + commission + swap): {net:.2f}")
    print()

    # List recent deals (last 30) for inspection
    by_time = sorted(trading_deals, key=lambda d: d.get("time") or "")
    print("=== RECENT TRADES (up to 30) ===")
    for d in by_time[-30:]:
        t = d.get("time", "")[:19].replace("T", " ")
        typ = d.get("type", "")
        sym = d.get("symbol", "?")
        vol = d.get("volume", 0)
        pr = d.get("price", 0)
        pnl = d.get("profit", 0)
        print(f"  {t}  {typ:20}  {sym:12}  vol={vol}  price={pr}  profit={pnl:.2f}")
    print()

    # --- Analysis ---
    closing = [d for d in trading_deals if (d.get("profit") or 0) != 0]
    winners = [d for d in closing if (d.get("profit") or 0) > 0]
    losers = [d for d in closing if (d.get("profit") or 0) < 0]
    gross_win = sum(d.get("profit") or 0 for d in winners)
    gross_loss = sum(d.get("profit") or 0 for d in losers)
    print("=== ANALYSIS ===")
    print(f"Round-trips with P&L: {len(closing)}  (entry deals have profit=0)")
    print(f"  Winning closes: {len(winners)}  Sum: {gross_win:.2f}")
    print(f"  Losing closes:  {len(losers)}  Sum: {gross_loss:.2f}")
    if winners:
        print(f"  Avg win:  {gross_win / len(winners):.2f}")
    if losers:
        print(f"  Avg loss: {gross_loss / len(losers):.2f}")
    win_rate = 100 * len(winners) / len(closing) if closing else 0
    print(f"  Win rate: {win_rate:.1f}%")
    by_day = defaultdict(float)
    for d in trading_deals:
        day = (d.get("time") or "")[:10]
        by_day[day] += d.get("profit") or 0
    print("  P&L by day:")
    for day in sorted(by_day.keys()):
        print(f"    {day}: {by_day[day]:.2f}")
    print()
    print("Done.")

if __name__ == "__main__":
    main()
