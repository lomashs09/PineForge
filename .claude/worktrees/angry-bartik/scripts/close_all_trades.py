"""
Close all open positions for the configured symbol (XAUUSDm by default).
Uses .env for METAAPI_TOKEN and METAAPI_ACCOUNT_ID.

Usage (from project root):
  python scripts/close_all_trades.py

Or with custom symbol:
  SYMBOL=XAUUSDm python scripts/close_all_trades.py
"""

import asyncio
import os
import sys
from pathlib import Path

# allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = os.getenv("PINEFORGE_SYMBOL", "XAUUSDm")


async def main():
    from metaapi_cloud_sdk import MetaApi

    if not TOKEN or not ACCOUNT_ID:
        print("ERROR: Set METAAPI_TOKEN and METAAPI_ACCOUNT_ID in .env")
        sys.exit(1)

    print(f"Closing all open positions for {SYMBOL}...")
    api = MetaApi(token=TOKEN)
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state not in ("DEPLOYING", "DEPLOYED"):
        print("Account not deployed. Nothing to close.")
        return

    await account.wait_connected(timeout_in_seconds=60)
    connection = account.get_rpc_connection()
    await connection.connect()
    try:
        await asyncio.wait_for(connection.wait_synchronized(timeout_in_seconds=60), timeout=65)
    except asyncio.TimeoutError:
        print("Sync timeout; attempting close anyway...")

    try:
        positions = await asyncio.wait_for(connection.get_positions(), timeout=15)
        symbol_positions = [p for p in (positions or []) if p.get("symbol") == SYMBOL]
    except Exception as e:
        print(f"ERROR getting positions: {e}")
        return

    if not symbol_positions:
        print(f"No open positions for {SYMBOL}.")
        return

    print(f"Found {len(symbol_positions)} position(s) for {SYMBOL}. Closing...")
    try:
        await asyncio.wait_for(
            connection.close_positions_by_symbol(SYMBOL),
            timeout=30,
        )
        print("All positions closed.")
    except Exception as e:
        print(f"ERROR closing: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
