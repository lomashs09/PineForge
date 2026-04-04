# PineForge Bot Worker

Runs trading bots on a Windows machine with direct MT5 terminal access.
Replaces MetaAPI entirely — no cloud dependency, no deploy/undeploy cycles.

## Architecture

```
API Server (Oracle VM)              Bot Worker (Windows VPS)
  │                                   │
  │ POST /bots/{id}/start             │ Polls DB every 5s
  │ → sets status="start_requested"   │ → sees "start_requested"
  │                                   │ → starts LiveBridge + DirectExecutor
  │                                   │ → MT5 terminal executes trades
  │                                   │ → writes logs/trades to DB
  │                                   │
  └───────── Neon DB (shared) ────────┘
```

## Setup on Windows VPS

### 1. Install MT5
- Download from https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
- Install, login to your Exness account
- Keep MT5 running

### 2. Install Python
- Download Python 3.12 from https://python.org
- Check "Add to PATH" during install

### 3. Clone and install
```cmd
git clone https://github.com/lomashs09/PineForge.git
cd PineForge
pip install -r requirements.txt
pip install MetaTrader5
```

### 4. Configure .env
```env
DATABASE_URL=postgresql+asyncpg://neondb_owner:npg_sZR1Sm4eyjAi@ep-wispy-dream-amobarbx-pooler.c-5.us-east-1.aws.neon.tech/neondb
WORKER_ID=worker-1
WORKER_POLL_INTERVAL=5
WORKER_MAX_BOTS=50
```

### 5. Run
```cmd
python -m worker.main
```

### 6. Set MT5_BACKEND on API server
In the API's `.env`:
```env
MT5_BACKEND=direct
```
Restart the API. Now bot start/stop goes through DB → worker picks it up.

## Cost
$8/mo (Contabo Windows VPS) replaces $15-20/account/mo (MetaAPI)
