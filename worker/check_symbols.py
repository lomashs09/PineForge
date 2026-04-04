"""Quick script to check available symbols in MT5 terminal."""
import MetaTrader5 as mt5

mt5.initialize()
symbols = mt5.symbols_get()
print(f"Total symbols: {len(symbols)}\n")

# Search for common ones
for search in ["BTC", "XAU", "EUR", "GBP", "USD"]:
    matches = [s.name for s in symbols if search in s.name and s.visible]
    if matches:
        print(f"{search}: {', '.join(matches[:10])}")

mt5.shutdown()
