"""
Standalone MetaAPI connection & order test.
Tests: connect, account info, place BUY, check positions, close, place SELL, close.
"""

import asyncio
import os
import sys
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.WARNING)

load_dotenv()

TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSDm"
LOT = 0.01


async def main():
    from metaapi_cloud_sdk import MetaApi

    if not TOKEN or not ACCOUNT_ID:
        print("ERROR: Set METAAPI_TOKEN and METAAPI_ACCOUNT_ID in .env")
        sys.exit(1)

    print(f"Token: ...{TOKEN[-20:]}")
    print(f"Account ID: {ACCOUNT_ID}")
    print(f"Symbol: {SYMBOL}, Lot: {LOT}")
    print()

    # --- Step 1: Connect ---
    print("[1/7] Creating MetaApi instance...")
    api = MetaApi(token=TOKEN)

    print("[2/7] Getting account...")
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
    print(f"  State: {account.state}")
    print(f"  Connection status: {account.connection_status}")

    if account.state not in ("DEPLOYING", "DEPLOYED"):
        print("  Deploying account...")
        try:
            await account.deploy()
        except Exception as e:
            print(f"  Deploy note: {e}")

    print("[3/7] Waiting for account connection (up to 120s)...")
    await account.wait_connected(timeout_in_seconds=120)
    print("  Account connected!")

    print("[4/7] Creating RPC connection & synchronizing...")
    connection = account.get_rpc_connection()
    await connection.connect()
    try:
        await connection.wait_synchronized(timeout_in_seconds=120)
        print("  Synchronized!")
    except Exception as e:
        print(f"  Sync warning: {e}")
        print("  Continuing anyway — will try direct API calls...")

    # --- Step 2: Account info ---
    print("\n[5/7] Getting account info...")
    try:
        info = await connection.get_account_information()
        print(f"  Balance:  {info.get('balance', '?')}")
        print(f"  Equity:   {info.get('equity', '?')}")
        print(f"  Currency: {info.get('currency', '?')}")
        print(f"  Leverage: {info.get('leverage', '?')}")
        print(f"  Server:   {info.get('server', '?')}")
    except Exception as e:
        print(f"  ERROR getting account info: {e}")
        print("  Cannot proceed without account info. Exiting.")
        return

    # --- Step 3: Place a test BUY ---
    print(f"\n[6/7] Placing test BUY: {SYMBOL} @ {LOT} lots...")
    try:
        result = await connection.create_market_buy_order(
            symbol=SYMBOL,
            volume=LOT,
        )
        print(f"  Result: {result}")
        order_id = result.get("orderId")
        position_id = result.get("positionId")
        print(f"  Order ID:    {order_id}")
        print(f"  Position ID: {position_id}")
    except Exception as e:
        print(f"  ERROR placing BUY: {e}")
        print("  Order test FAILED.")
        return

    # --- Check positions ---
    print("\n  Checking open positions...")
    await asyncio.sleep(2)
    try:
        positions = await connection.get_positions()
        print(f"  Open positions: {len(positions)}")
        for p in positions:
            print(f"    {p.get('type')} {p.get('symbol')} vol={p.get('volume')} "
                  f"openPrice={p.get('openPrice')} profit={p.get('profit')}")
    except Exception as e:
        print(f"  ERROR getting positions: {e}")

    # --- Close the BUY ---
    print(f"\n  Closing BUY position {position_id}...")
    await asyncio.sleep(1)
    try:
        close_result = await connection.close_position(position_id)
        print(f"  Close result: {close_result}")
    except Exception as e:
        print(f"  ERROR closing position: {e}")

    # --- Step 4: Place a test SELL ---
    print(f"\n[7/7] Placing test SELL: {SYMBOL} @ {LOT} lots...")
    try:
        result = await connection.create_market_sell_order(
            symbol=SYMBOL,
            volume=LOT,
        )
        print(f"  Result: {result}")
        sell_pos_id = result.get("positionId")
        print(f"  Position ID: {sell_pos_id}")
    except Exception as e:
        print(f"  ERROR placing SELL: {e}")
        print("  SELL test FAILED.")
        return

    await asyncio.sleep(2)

    print(f"  Closing SELL position {sell_pos_id}...")
    try:
        close_result = await connection.close_position(sell_pos_id)
        print(f"  Close result: {close_result}")
    except Exception as e:
        print(f"  ERROR closing position: {e}")

    # --- Final check ---
    print("\n  Final positions check...")
    try:
        positions = await connection.get_positions()
        print(f"  Open positions: {len(positions)}")
        for p in positions:
            print(f"    {p.get('type')} {p.get('symbol')} vol={p.get('volume')} profit={p.get('profit')}")
    except Exception as e:
        print(f"  ERROR: {e}")

    print("\n" + "=" * 50)
    print("TEST COMPLETE")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
