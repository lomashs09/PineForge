#!/usr/bin/env python3
"""Example: run the SMA crossover strategy against sample data."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pineforge.engine import Engine
from pineforge.data import load_csv

SCRIPT = (Path(__file__).parent / "sma_crossover.pine").read_text()
DATA_PATH = Path(__file__).parent / "sample_data.csv"


def main():
    if not DATA_PATH.exists():
        print("Generating sample data...")
        _generate_sample_data()

    data = load_csv(DATA_PATH)
    engine = Engine(initial_capital=10000.0, commission=0.001)
    result = engine.run(SCRIPT, data)

    print(result.summary())
    print()
    print(result.trade_log())


def _generate_sample_data():
    """Generate synthetic OHLCV data for testing."""
    import math
    import csv

    rows = 500
    base_price = 100.0

    with open(DATA_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])

        price = base_price
        for i in range(rows):
            date = f"2024-01-01T00:00:00"
            trend = math.sin(i / 40) * 5
            noise = ((i * 7 + 3) % 11 - 5) * 0.5
            price = base_price + trend + noise

            o = round(price + ((i * 3) % 7 - 3) * 0.2, 2)
            c = round(price + ((i * 5) % 9 - 4) * 0.3, 2)
            h = round(max(o, c) + abs((i * 11) % 13) * 0.1, 2)
            l = round(min(o, c) - abs((i * 13) % 11) * 0.1, 2)
            v = 1000 + (i * 17) % 500

            day_offset = i
            year = 2024 + day_offset // 365
            remainder = day_offset % 365
            month = remainder // 30 + 1
            day = remainder % 30 + 1
            if month > 12:
                month = 12
            if day > 28:
                day = 28
            date = f"{year}-{month:02d}-{day:02d}"

            writer.writerow([date, o, h, l, c, v])

    print(f"Sample data written to {DATA_PATH} ({rows} bars)")


if __name__ == "__main__":
    main()
