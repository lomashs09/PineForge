# MT5 Bridge — Self-Hosted MetaTrader 5 REST API

Replaces MetaAPI cloud by running MT5 in Docker (Wine on Linux) and exposing a REST API for order execution, position management, and historical data.

## Architecture

```
PineForge API (backend)
     │
     │  HTTP calls (instead of MetaAPI SDK)
     ▼
MT5 Bridge (FastAPI, port 5555)
     │
     │  MetaTrader5 Python package (via Wine)
     ▼
MT5 Terminal (running in Docker via Wine)
     │
     │  Broker protocol
     ▼
Exness MT5 Server
```

One bridge instance = one MT5 account. For multiple accounts, run multiple Docker containers.

## Requirements

- **x86-64 host** (Wine does not work on ARM)
- Docker + Docker Compose
- ~1GB RAM per account

## Quick Start

```bash
cd mt5bridge
cp .env.example .env
# Edit .env with your MT5 credentials

docker-compose up -d
# Wait ~60s for MT5 to initialize

curl http://localhost:5551/health
# {"status": "ok", "connected": true, "login": 413471385, "server": "Exness-MT5Trial6"}
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Connection status |
| POST | `/connect` | Connect to MT5 account `{login, password, server}` |
| POST | `/disconnect` | Disconnect from MT5 |
| GET | `/account` | Balance, equity, margin |
| POST | `/order/buy` | Market buy `{symbol, volume}` |
| POST | `/order/sell` | Market sell `{symbol, volume}` |
| GET | `/positions?symbol=XAUUSDm` | List open positions |
| POST | `/positions/close` | Close all positions for symbol `{symbol}` |
| POST | `/candles` | Historical OHLCV `{symbol, timeframe, count}` |

## PineForge Integration

Set these in PineForge's `.env`:

```env
MT5_BACKEND=bridge
MT5_BRIDGE_URL=http://localhost:5551
```

The bot manager will use the bridge instead of MetaAPI for all MT5 operations.

## Cost Savings

| Accounts | MetaAPI Cost | Self-Hosted Cost | Savings |
|----------|-------------|-----------------|---------|
| 1 | ~$15-20/mo | ~$5-8/mo (VPS) | 60-75% |
| 10 | ~$150-200/mo | ~$15-30/mo | 85-90% |
| 50 | ~$750+/mo | ~$50-80/mo | 90%+ |
