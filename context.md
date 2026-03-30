# PineForge Cloud — Multi-Tenant Trading Bot Platform

## Vision

Transform PineForge from a single-user CLI backtesting/live-trading tool into a **SaaS platform** where users sign up, connect their broker accounts (Exness via MetaAPI), pick or upload Pine Script strategies, configure and launch live trading bots — all managed through a REST API with JWT authentication.

Phase 1 target: **10 concurrent bots** across multiple users, scalable to 100+.

---

## Implementation Status: COMPLETE (Phase 1)

All 7 phases have been implemented and tested:

- **Phase 1**: Database foundation (config, models, Alembic migrations) -- DONE
- **Phase 2**: JWT authentication (register, login, refresh, profile) -- DONE
- **Phase 3**: Scripts CRUD + backtest endpoint -- DONE
- **Phase 4**: Broker accounts CRUD + MetaAPI provisioning -- DONE
- **Phase 5**: LiveBridge modification + bot logger -- DONE
- **Phase 6**: BotManager singleton + bot CRUD API -- DONE
- **Phase 7**: Dashboard + admin endpoints + deploy service -- DONE

**35 API endpoints** implemented, **34 new Python files** created, **4 existing files** modified.

---

## Existing Codebase (Core Engine — Unchanged)

### Project Structure

```
PineForge/
├── pineforge/                  # Core engine (Python package)
│   ├── __main__.py             # CLI entry point (run, download, live)
│   ├── engine.py               # Backtesting engine — bar-by-bar execution
│   ├── interpreter.py          # Pine Script v5 interpreter
│   ├── lexer.py                # Tokenizer for Pine Script
│   ├── parser.py               # AST parser
│   ├── ast_nodes.py            # AST node definitions
│   ├── series.py               # Time series data type (like Pine's series)
│   ├── environment.py          # Variable scoping / symbol table
│   ├── broker.py               # Simulated broker (position tracking, PnL, fill logic)
│   ├── data.py                 # Data feed: Yahoo Finance download + CSV loader
│   ├── results.py              # Backtest result computation (Sharpe, PF, DD, etc.)
│   ├── tokens.py               # Token types for lexer
│   ├── builtins/
│   │   ├── strategy.py         # strategy.entry(), strategy.exit(), strategy.close()
│   │   ├── ta.py               # Technical analysis: EMA, SMA, RSI, ATR, MACD, etc.
│   │   ├── math_funcs.py       # math.abs, math.max, math.min, math.floor, nz()
│   │   └── input_funcs.py      # input.int(), input.float()
│   └── live/
│       ├── bridge.py           # LiveBridge — main async loop connecting strategy → MetaAPI
│       ├── config.py           # LiveConfig dataclass + .env loader
│       ├── executor.py         # Order executor: open_buy, open_sell, close_all via MetaAPI
│       ├── feed.py             # Candle fetch from MetaAPI, new-bar detection
│       └── risk.py             # RiskManager: daily loss limit, cooldown, position sizing
├── examples/                   # 26 Pine Script strategy files (seeded as system scripts)
├── tests/                      # 5 test files (test_lexer, test_parser, test_engine, test_interpreter, test_accuracy)
├── scripts/
│   ├── close_all_trades.py     # Utility to close all MetaAPI positions
│   └── fetch_trade_history.py  # Utility to fetch trade history
├── deploy/
│   ├── pineforge.service       # systemd unit file for single-bot deployment (legacy)
│   ├── pineforge-api.service   # NEW — systemd unit file for FastAPI server
│   └── COMMANDS.md             # Server deployment commands
│
├── api/                        # NEW — FastAPI application (see below)
├── alembic/                    # NEW — Database migrations (see below)
├── alembic.ini                 # NEW — Alembic config
├── requirements.txt            # Updated with API dependencies
├── pyproject.toml              # Python >=3.9, entry point: pineforge.__main__:main
├── .env                        # METAAPI_TOKEN, METAAPI_ACCOUNT_ID, DATABASE_URL, JWT_SECRET_KEY, etc.
├── .env.example                # Template for .env
└── context.md                  # This file
```

### Key Interfaces (Core Engine)

**Engine** (`pineforge/engine.py`):
```python
class Engine:
    def __init__(self, initial_capital=10000.0, commission=0.0, slippage=0.0, fill_on="next_open", interval="1d")
    def run(self, script_source: str, data: DataFeed, input_overrides: dict | None = None) -> BacktestResult
```

**BacktestResult** (`pineforge/results.py`):
```python
@dataclass
class BacktestResult:
    strategy_name: str, initial_capital: float, final_equity: float,
    total_trades: int, winning_trades: int, losing_trades: int,
    gross_profit: float, gross_loss: float, net_profit: float,
    win_rate: float, profit_factor: float, total_return_pct: float,
    max_drawdown: float, max_drawdown_pct: float, sharpe_ratio: float,
    avg_trade_pnl: float, avg_winning_trade: float, avg_losing_trade: float,
    trades: list[Trade], equity_curve: list[float]
```

**Trade** (`pineforge/broker.py`):
```python
@dataclass
class Trade:
    direction: str, entry_price: float, exit_price: float | None,
    entry_bar: int, exit_bar: int | None, entry_date: Any, exit_date: Any,
    pnl: float, pnl_pct: float
```

**DataFeed** (`pineforge/data.py`):
```python
class DataFeed:
    def __init__(self, bars: list[dict])  # bars: {open, high, low, close, volume, date}
# download(symbol, start, end, interval, output) -> DataFeed  (uses yfinance)
# load_csv(path) -> DataFeed
```

**LiveBridge** (`pineforge/live/bridge.py`) — **MODIFIED for API**:
```python
class LiveBridge:
    def __init__(self, config: LiveConfig)
        self._shutdown = False
        self._register_signals = True  # Set False by BotManager (avoids signal handler crash in asyncio tasks)
        self._bar_count = 0
        self._poll_count = 0
        self._start_time = None
        self._pending_signal = None
    async def run(self)  # Main loop: connects MetaAPI, polls, executes signals
```

**LiveConfig** (`pineforge/live/config.py`) — **MODIFIED for API**:
```python
@dataclass
class LiveConfig:
    metaapi_token: str = ""
    metaapi_account_id: str = ""
    symbol: str = "XAUUSDm"
    timeframe: str = "1h"
    lot_size: float = 0.01
    max_lot_size: float = 0.1
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 1
    cooldown_seconds: int = 60
    is_live: bool = False
    poll_interval_seconds: int = 60
    lookback_bars: int = 200
    script_path: str = ""
    script_source: str = ""  # NEW — if set, used instead of script_path (for API usage)
```

### How the Live Trading Bridge Works

1. `LiveBridge.__init__()` creates its own isolated state: interpreter, broker, series objects
2. `_init_interpreter()` parses the Pine Script (from `script_source` or `script_path`) and sets up the execution environment
3. `run()` connects to MetaAPI, fetches warmup bars, enters the poll loop
4. Each poll cycle (`_poll_cycle`):
   - Fetches latest candles from MetaAPI
   - Detects new bar via timestamp comparison
   - Executes the *previous* bar's queued signal at current bar's open (next-bar-open semantics)
   - Feeds the new bar through the interpreter to compute the *next* signal
5. Signals are executed via the `Executor` (market buy/sell/close via MetaAPI RPC)
6. `RiskManager` enforces daily loss limits, cooldowns, and max position counts

### Key Architectural Facts

- `LiveBridge` is **fully self-contained** — each instance has its own interpreter, broker, series state. Multiple instances coexist in one process.
- The bridge is **async** (`asyncio`) — perfect for running many bots in a single event loop.
- MetaAPI connections are per-account. Each bot needs its own `metaapi_account_id`.
- A single `METAAPI_TOKEN` (API key) can manage multiple MetaAPI accounts.
- Each MetaAPI account corresponds to one Exness MT5 login.
- **Engine global state**: `get_strategy_context()`, `get_ta_state()` are module-level singletons. They are reset at the start of each `Engine.run()` and `LiveBridge._init_interpreter()`. Backtests are serialized with `asyncio.Lock` in the API.
- **Modifications made to LiveBridge for API**: (1) `script_source` field on LiveConfig to accept script text from DB instead of file path. (2) `_register_signals` flag to skip OS signal handler registration when running inside BotManager (avoids `ValueError: signal only works in main thread`).

---

## API Layer (NEW — `api/`)

### Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| API Framework | **FastAPI** | Async-native, auto-generated OpenAPI docs at `/docs` |
| Database | **PostgreSQL** (local) | DB name: `pineforge`, user: `apple` (macOS dev), no password |
| ORM | **SQLAlchemy 2.0** (async) | `asyncpg` driver, `async_sessionmaker` |
| Migrations | **Alembic** | Async env, initial migration: `1ce30ff1e59b_initial_schema.py` |
| Auth | **JWT** (`python-jose` + `passlib[bcrypt]`) | Access + refresh tokens, bcrypt password hashing |
| Bot Runtime | **asyncio tasks** within FastAPI | LiveBridge wrapped by `BotManager` singleton |
| Validation | **Pydantic v2** + `pydantic-settings` | Request/response schemas, env config |
| Python | **3.9+** | Uses `Optional[X]` (not `X | None`) in ORM models for 3.9 compat. `from __future__ import annotations` used only in `pineforge/live/` files. |

### API Directory Structure

```
api/
├── __init__.py
├── config.py               # Pydantic BaseSettings: DATABASE_URL, JWT_SECRET_KEY, METAAPI_TOKEN, etc.
├── database.py             # Async SQLAlchemy engine + async_sessionmaker + get_db dependency + Base
├── main.py                 # FastAPI app factory, lifespan (seed scripts, init BotManager, restart bots)
│
├── models/
│   ├── __init__.py         # Re-exports all models for Alembic discovery
│   ├── user.py             # User model (UUID PK, email, hashed_password, max_bots, is_admin)
│   ├── broker_account.py   # BrokerAccount model (metaapi_account_id, mt5_login, mt5_server)
│   ├── script.py           # Script model (source TEXT, is_system, is_public)
│   ├── bot.py              # Bot model (symbol, timeframe, lot_size, status, all risk params)
│   ├── bot_log.py          # BotLog model (BIGSERIAL PK, bot_id FK, level, message, metadata JSONB)
│   └── bot_trade.py        # BotTrade model (direction, entry/exit_price, pnl, order_id)
│
├── schemas/
│   ├── __init__.py
│   ├── auth.py             # RegisterRequest, LoginRequest, TokenResponse, UserResponse, etc.
│   ├── script.py           # ScriptCreate, ScriptResponse, BacktestRequest, BacktestResponse, etc.
│   ├── broker_account.py   # AccountProvisionRequest, AccountResponse, AccountDetailResponse
│   ├── bot.py              # BotCreate, BotResponse, BotStatusResponse, BotLogsPage, BotStatsResponse
│   └── dashboard.py        # DashboardResponse
│
├── routers/
│   ├── __init__.py
│   ├── auth.py             # /api/auth/* (register, login, refresh, me GET+PATCH)
│   ├── scripts.py          # /api/scripts/* (CRUD + POST /{id}/backtest)
│   ├── accounts.py         # /api/accounts/* (CRUD + GET /{id}/positions)
│   ├── bots.py             # /api/bots/* (CRUD + start/stop/logs/trades/stats)
│   ├── dashboard.py        # /api/dashboard (aggregate stats)
│   └── admin.py            # /api/admin/* (users, bots, user update, system scripts)
│
├── services/
│   ├── __init__.py
│   ├── auth_service.py     # hash_password, verify_password, create_access_token, create_refresh_token, decode_token
│   ├── script_service.py   # validate_script (Lexer+Parser), seed_system_scripts (26 .pine files), run_backtest (asyncio.Lock + thread executor)
│   ├── account_service.py  # provision_account (MetaAPI), get_account_info, get_account_positions
│   ├── bot_service.py      # validate_bot_create (max_bots, ownership), get_bot_stats (aggregate trades)
│   └── bot_manager.py      # BotManager singleton (start_bot, stop_bot, get_status, restart_crashed_bots, shutdown_all)
│
├── middleware/
│   ├── __init__.py
│   └── auth.py             # get_current_user (JWT dependency), get_current_admin (403 if not admin)
│
└── utils/
    ├── __init__.py
    └── bot_logger.py       # BotDatabaseHandler (batch-insert logs via asyncio.Queue), BotPrintCapture (stdout → logger)
```

### Alembic Structure

```
alembic/
├── env.py                          # Async migration env (imports api.models, uses asyncpg)
├── script.py.mako                  # Migration template
└── versions/
    └── 1ce30ff1e59b_initial_schema.py  # Creates all 6 tables
alembic.ini                         # Config at project root
```

---

## Database Schema

### `users`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK, `gen_random_uuid()` |
| email | VARCHAR(255) | Unique, indexed |
| hashed_password | VARCHAR(255) | bcrypt hash |
| full_name | VARCHAR(100) | |
| is_active | BOOLEAN | Default true |
| is_admin | BOOLEAN | Default false |
| max_bots | INTEGER | Default 3 |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | onupdate |

### `broker_accounts`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK → users.id CASCADE |
| label | VARCHAR(100) | User-friendly name |
| metaapi_account_id | VARCHAR(100) | MetaAPI provisioned account ID |
| mt5_login | VARCHAR(50) | Exness MT5 login number |
| mt5_server | VARCHAR(100) | e.g. "Exness-MT5Real" |
| broker_name | VARCHAR(50) | Default "exness" |
| is_active | BOOLEAN | Default true (soft delete) |
| created_at | TIMESTAMPTZ | |

### `scripts`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK → users.id, NULL for system scripts |
| name | VARCHAR(100) | |
| filename | VARCHAR(255) | Auto-generated from name |
| source | TEXT | Full Pine Script source code |
| description | TEXT | Optional |
| is_system | BOOLEAN | True for built-in strategies (26 seeded from examples/) |
| is_public | BOOLEAN | True if shared |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `bots`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK → users.id |
| broker_account_id | UUID | FK → broker_accounts.id |
| script_id | UUID | FK → scripts.id |
| name | VARCHAR(100) | |
| symbol | VARCHAR(20) | e.g. "XAUUSDm" |
| timeframe | VARCHAR(10) | e.g. "1h", "5m" |
| lot_size | NUMERIC(10,4) | |
| max_lot_size | NUMERIC(10,4) | Default 0.1 |
| max_daily_loss_pct | NUMERIC(5,2) | Default 5.00 |
| max_open_positions | INTEGER | Default 1 |
| cooldown_seconds | INTEGER | Default 60 |
| poll_interval_seconds | INTEGER | Default 60 |
| lookback_bars | INTEGER | Default 200 |
| is_live | BOOLEAN | False = dry run |
| status | VARCHAR(20) | "stopped", "starting", "running", "error", "stopping" |
| started_at | TIMESTAMPTZ | |
| stopped_at | TIMESTAMPTZ | |
| error_message | TEXT | Last error if status = "error" |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `bot_logs`

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | Auto-increment PK |
| bot_id | UUID | FK → bots.id, indexed |
| level | VARCHAR(10) | "info", "signal", "trade", "error", "heartbeat" |
| message | TEXT | |
| metadata | JSONB | Optional structured data |
| created_at | TIMESTAMPTZ | Indexed (composite index with bot_id) |

### `bot_trades`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| bot_id | UUID | FK → bots.id |
| broker_account_id | UUID | FK → broker_accounts.id |
| direction | VARCHAR(5) | "long" or "short" |
| symbol | VARCHAR(20) | |
| lot_size | NUMERIC(10,4) | |
| entry_price | NUMERIC(20,5) | |
| exit_price | NUMERIC(20,5) | NULL if still open |
| pnl | NUMERIC(20,5) | NULL if still open |
| signal | VARCHAR(20) | "entry_long", "entry_short", "close" |
| order_id | VARCHAR(100) | MetaAPI/MT5 order ID |
| opened_at | TIMESTAMPTZ | |
| closed_at | TIMESTAMPTZ | NULL if still open |

---

## API Endpoints (All 35 Routes)

### Authentication (`/api/auth`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/api/auth/register` | Create new user account | Public |
| POST | `/api/auth/login` | Get JWT access + refresh tokens | Public |
| POST | `/api/auth/refresh` | Refresh expired access token | Refresh token |
| GET | `/api/auth/me` | Get current user profile | JWT |
| PATCH | `/api/auth/me` | Update profile (name, password) | JWT |

**Register request:**
```json
{ "email": "trader@example.com", "password": "securepassword123", "full_name": "John Trader" }
```

**Login response:**
```json
{ "access_token": "eyJhbG...", "refresh_token": "eyJhbG...", "token_type": "bearer", "expires_in": 3600 }
```

**JWT payload:** `{ "sub": "user-uuid", "email": "...", "is_admin": false, "exp": ..., "type": "access"|"refresh" }`

### Scripts (`/api/scripts`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/scripts` | List system + user's scripts (no source in list) | JWT |
| GET | `/api/scripts/{id}` | Get script with full source code | JWT |
| POST | `/api/scripts` | Upload a custom Pine Script (validated via Lexer+Parser) | JWT |
| PUT | `/api/scripts/{id}` | Update a user's script | JWT |
| DELETE | `/api/scripts/{id}` | Delete a user's script (not system) | JWT |
| POST | `/api/scripts/{id}/backtest` | Run backtest, returns full results | JWT |

**Backtest request:**
```json
{ "symbol": "XAUUSD", "interval": "1h", "start": "2025-01-06", "end": "2025-12-31", "capital": 10000 }
```

**Backtest response:**
```json
{
  "strategy_name": "Gold Trend Hunter", "total_return_pct": 40.21, "total_trades": 157,
  "win_rate_pct": 43.95, "profit_factor": 1.62, "max_drawdown_pct": 11.18,
  "sharpe_ratio": 1.20, "net_profit": 4020.59, "initial_capital": 10000.0,
  "final_equity": 14020.59, "winning_trades": 69, "losing_trades": 88,
  "avg_trade_pnl": 25.61,
  "trades": [{ "direction": "long", "entry_price": 2665.30, "exit_price": 2710.80, "pnl": 145.50, ... }]
}
```

### Broker Accounts (`/api/accounts`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/accounts` | List user's broker accounts | JWT |
| POST | `/api/accounts` | Provision a new Exness account via MetaAPI | JWT |
| GET | `/api/accounts/{id}` | Get account details + live balance from MetaAPI | JWT |
| DELETE | `/api/accounts/{id}` | Soft-delete account (must have no running bots) | JWT |
| GET | `/api/accounts/{id}/positions` | Get open positions from MT5 via MetaAPI | JWT |

**Provision request:**
```json
{ "label": "My Gold Account", "mt5_login": "12345678", "mt5_password": "mypassword", "mt5_server": "Exness-MT5Real" }
```

The server calls MetaAPI to provision the account, stores only the returned `metaapi_account_id` (never stores the MT5 password).

### Bots (`/api/bots`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/bots` | List user's bots (with status) | JWT |
| POST | `/api/bots` | Create a new bot | JWT |
| GET | `/api/bots/{id}` | Get bot config + current status | JWT |
| PATCH | `/api/bots/{id}` | Update bot config (only when stopped) | JWT |
| DELETE | `/api/bots/{id}` | Delete a bot (must be stopped) | JWT |
| POST | `/api/bots/{id}/start` | Start the bot | JWT |
| POST | `/api/bots/{id}/stop` | Stop the bot | JWT |
| GET | `/api/bots/{id}/logs` | Get bot logs (paginated, filterable by level) | JWT |
| GET | `/api/bots/{id}/trades` | Get bot's trade history | JWT |
| GET | `/api/bots/{id}/stats` | Get bot's performance stats | JWT |

**Create bot request:**
```json
{
  "name": "Gold Hunter 1H", "broker_account_id": "uuid", "script_id": "uuid",
  "symbol": "XAUUSDm", "timeframe": "1h", "lot_size": 0.03, "is_live": false,
  "max_daily_loss_pct": 5.0, "poll_interval_seconds": 60
}
```

**Start bot response:**
```json
{
  "id": "uuid", "name": "Gold Hunter 1H", "status": "starting", "symbol": "XAUUSDm",
  "timeframe": "1h", "lot_size": 0.03, "is_live": false,
  "started_at": "2026-03-28T14:00:00Z", "uptime_seconds": 0, "bars_processed": 0
}
```

**Get logs (paginated):** `GET /api/bots/{id}/logs?limit=50&offset=0&level=trade`

### Dashboard (`/api/dashboard`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/dashboard` | User's aggregate stats | JWT |

**Response:**
```json
{ "active_bots": 3, "total_bots": 5, "broker_accounts": 2, "today_pnl": 45.30, "total_pnl": 1250.80, "total_trades": 342, "win_rate_pct": 48.5 }
```

### Admin (`/api/admin`) — Admin JWT Required

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/admin/users` | List all users with bot counts | Admin JWT |
| GET | `/api/admin/bots` | List all running bots across all users | Admin JWT |
| PATCH | `/api/admin/users/{id}` | Update user (max_bots, is_active, is_admin) | Admin JWT |
| POST | `/api/admin/scripts` | Add a new system script | Admin JWT |

### Health

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/health` | Health check | Public |

---

## Bot Lifecycle Management

### BotManager Singleton (`api/services/bot_manager.py`)

Lives inside the FastAPI app (`app.state.bot_manager`) and manages bot asyncio tasks:

```
BotManager
├── _running_bots: dict[UUID, asyncio.Task]
├── _bot_bridges: dict[UUID, LiveBridge]
├── _bot_loggers: dict[UUID, BotDatabaseHandler]
├── _session_factory: async_sessionmaker
├── _metaapi_token: str
│
├── start_bot(bot_id) → None
│   1. Load bot + broker_account + script from DB (selectinload)
│   2. Build LiveConfig(script_source=script.source, metaapi_token=PLATFORM_TOKEN, ...)
│   3. Create LiveBridge(config), set _register_signals=False
│   4. Create logger bot.{bot_id}, attach BotDatabaseHandler
│   5. Create asyncio.Task wrapping _run_bot_wrapper()
│   6. Update bot status → "starting" then "running" in DB
│
├── stop_bot(bot_id) → None
│   1. Set bridge._shutdown = True (graceful shutdown flag)
│   2. Wait up to 30s for task completion
│   3. If still running, cancel the task
│   4. Update bot status → "stopped" in DB
│
├── get_status(bot_id) → dict | None
│   Returns: running, uptime_seconds, bars_processed, polls, last_signal
│
├── restart_crashed_bots() → None
│   On startup: query DB for bots with status="running"/"starting", restart each
│
└── shutdown_all() → None
    On app shutdown: stop all running bots
```

### Bot Logging Integration (`api/utils/bot_logger.py`)

**BotDatabaseHandler** (logging.Handler):
- Receives log records via `emit()`, puts them in an `asyncio.Queue`
- Background consumer task batch-inserts to `bot_logs` table every 1s or 50 records
- Maps logging levels to custom bot levels: "info", "signal", "trade", "error", "heartbeat"

**BotPrintCapture** (io.TextIOBase):
- Writable stream that replaces `sys.stdout` within the bot coroutine
- Routes all `print()` output to the bot's logger
- Detects log levels from output patterns:
  - `[LIVE] BUY/SELL` or `[DRY RUN]` → "trade"
  - `Signal queued` / `Executing queued signal` → "signal"
  - `HEARTBEAT` → "heartbeat"
  - `[ERROR]` → "error"
  - Everything else → "info"

### MetaAPI Token Management

**Phase 1 — Platform token (current implementation):**
- One `METAAPI_TOKEN` stored as an environment variable
- All user accounts provisioned under this single token
- Token is never exposed via API responses

---

## Process Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Server                         │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │ Auth API │  │ Bot API  │  │Script API│  │Admin   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬───┘  │
│       │              │              │              │      │
│       ▼              ▼              ▼              ▼      │
│  ┌──────────────────────────────────────────────────┐    │
│  │          PostgreSQL (async via asyncpg)           │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │         Bot Manager (singleton on app.state)      │    │
│  │                                                    │    │
│  │  ┌─────────┐ ┌─────────┐ ┌────────┐              │    │
│  │  │ Bot #1  │ │ Bot #2  │ │ Bot #3 │  (asyncio    │    │
│  │  │LiveBrdg │ │LiveBrdg │ │LiveBrdg│   tasks)     │    │
│  │  │+Logger  │ │+Logger  │ │+Logger │              │    │
│  │  └─────────┘ └─────────┘ └────────┘              │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## Security Considerations

1. **MT5 passwords** — only used during MetaAPI provisioning, never stored in our DB
2. **MetaAPI token** — server-side only, never returned in API responses
3. **JWT secrets** — stored as environment variables, not in code
4. **User isolation** — every DB query filters by `user_id` from the JWT; users cannot access other users' bots/accounts/scripts
5. **Bot limits** — `max_bots` per user prevents resource abuse (default 3)
6. **Script validation** — Pine Script source is parsed (Lexer + Parser) before being stored
7. **Backtest serialization** — `asyncio.Lock` prevents engine global state race conditions
8. **Password hashing** — bcrypt via passlib (pinned bcrypt==4.0.1 for passlib compatibility)

---

## Environment Variables

```bash
# MetaAPI (platform-level token)
METAAPI_TOKEN=your-metaapi-token
METAAPI_ACCOUNT_ID=your-mt5-account-id  # for CLI usage

# Database
DATABASE_URL=postgresql+asyncpg://apple@localhost:5432/pineforge  # local dev (no password)
# DATABASE_URL=postgresql+asyncpg://pineforge:password@localhost:5432/pineforge  # production

# JWT
JWT_SECRET_KEY=your-random-secret-key-here
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# App
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
```

---

## Dependencies

```
# Existing (core engine)
pandas>=2.0
yfinance>=0.2
metaapi-cloud-sdk>=29.0
python-dotenv>=1.0

# API
fastapi>=0.115.0
uvicorn[standard]>=0.34.0

# Database
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.30.0
alembic>=1.14.0

# Auth
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
bcrypt==4.0.1  # Pinned for passlib compatibility

# Validation
pydantic>=2.0.0
pydantic-settings>=2.0.0
email-validator>=2.0.0
```

---

## Startup Sequence

1. FastAPI app starts → `lifespan()` context manager runs
2. System scripts seeded from `examples/*.pine` (26 strategies, idempotent)
3. `BotManager` initialized with `async_sessionmaker` and `METAAPI_TOKEN`
4. `BotManager.restart_crashed_bots()` restarts any bots with status="running" in DB
5. API starts accepting requests
6. On shutdown: `BotManager.shutdown_all()` → `engine.dispose()`

**Start command:**
```bash
cd PineForge
alembic upgrade head
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Swagger UI: `http://localhost:8000/docs`

---

## Key Design Decisions & Gotchas

1. **Python 3.9 compatibility**: ORM models use `Optional[X]` not `X | None` (union syntax requires 3.10+). The `from __future__ import annotations` import is only used in `pineforge/live/` files (existing code) — NOT in API models because SQLAlchemy's `Mapped[T]` needs runtime type evaluation.

2. **Engine global state serialization**: `script_service.py` uses `_backtest_lock = asyncio.Lock()` and `run_in_executor()` to safely run the synchronous `Engine.run()` without corrupting module-level singletons.

3. **LiveBridge signal handlers**: `_register_signals` flag was added because `signal.signal()` can only be called from the main thread. When BotManager runs LiveBridge as an asyncio task, it sets this to `False`. The existing `_shutdown` flag still provides graceful stop capability.

4. **BotPrintCapture stdout redirect**: LiveBridge uses `print(..., flush=True)` extensively (27+ calls). Rather than rewriting all calls, BotManager redirects `sys.stdout` to a `BotPrintCapture` that routes output to the bot's logger, which writes to the `bot_logs` table.

5. **Batch log insertion**: `BotDatabaseHandler` uses an `asyncio.Queue` and background consumer to batch-insert log records, preventing DB pressure from high-frequency bots.

6. **bcrypt version pin**: `bcrypt==4.0.1` is pinned because newer versions (5.x) have a breaking API change that causes `passlib` to crash with `AttributeError: module 'bcrypt' has no attribute '__about__'`.

7. **Database session management**: `get_db()` dependency commits on success, rolls back on exception. BotManager uses its own `session_factory` for DB access outside the request/response cycle.

---

## Phase 1 Scope (Current Implementation)

**In scope (DONE):**
- User registration + JWT auth (register, login, refresh, me)
- Broker account provisioning (connect Exness via MetaAPI)
- Script management (list system scripts, upload custom, view source, validate)
- Bot CRUD (create, configure, start, stop, delete)
- Bot logs (persisted to DB, queryable via API with pagination + level filter)
- Bot trade history tracking
- Backtesting via API (runs engine in thread pool)
- Dashboard (aggregate stats per user)
- Admin panel (user management, system scripts)
- Single-server deployment (systemd service)

**Out of scope (Phase 2+):**
- WebSocket streaming for real-time bot logs
- Frontend/UI (Phase 1 is API-only, use Swagger UI at `/docs`)
- Per-user MetaAPI tokens
- Billing / subscription tiers
- Multi-server / horizontal scaling
- Strategy marketplace (users selling scripts)
- Notifications (email/Telegram alerts on trades)
- Two-factor authentication
- Rate limiting middleware

---

## Deployment (Phase 1)

Single VPS (e.g., Oracle Cloud, 4 CPU / 8 GB RAM):

```bash
# PostgreSQL
sudo apt install postgresql
sudo -u postgres createuser pineforge
sudo -u postgres createdb pineforge -O pineforge

# App
cd /home/ubuntu/PineForge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**systemd service** (`deploy/pineforge-api.service`):
```ini
[Unit]
Description=PineForge API Server
After=network-online.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/PineForge
EnvironmentFile=/home/ubuntu/PineForge/.env
ExecStart=/home/ubuntu/PineForge/venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Testing Strategy

- **Unit tests**: Auth service (JWT generation/validation), bot manager (start/stop logic)
- **Integration tests**: Full API endpoint tests with a test PostgreSQL database
- **Live dry-run test**: Create a bot in dry-run mode, verify logs appear in DB, verify no real orders
- **Live test**: Single bot with 0.01 lots on a demo Exness account to verify end-to-end flow

### Quick Smoke Test
```bash
# Start server
uvicorn api.main:app --port 8111

# Register
curl -X POST http://localhost:8111/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "testpass123", "full_name": "Test"}'

# Login
curl -X POST http://localhost:8111/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "testpass123"}'

# Use the access_token from login response as Bearer token for all other endpoints
```

---

## File Inventory

### New Files Created (34 Python + 4 config)

```
api/__init__.py
api/config.py
api/database.py
api/main.py
api/models/__init__.py
api/models/user.py
api/models/broker_account.py
api/models/script.py
api/models/bot.py
api/models/bot_log.py
api/models/bot_trade.py
api/schemas/__init__.py
api/schemas/auth.py
api/schemas/broker_account.py
api/schemas/script.py
api/schemas/bot.py
api/schemas/dashboard.py
api/services/__init__.py
api/services/auth_service.py
api/services/account_service.py
api/services/script_service.py
api/services/bot_service.py
api/services/bot_manager.py
api/routers/__init__.py
api/routers/auth.py
api/routers/accounts.py
api/routers/scripts.py
api/routers/bots.py
api/routers/dashboard.py
api/routers/admin.py
api/middleware/__init__.py
api/middleware/auth.py
api/utils/__init__.py
api/utils/bot_logger.py
alembic.ini
alembic/env.py
alembic/script.py.mako
alembic/versions/1ce30ff1e59b_initial_schema.py
deploy/pineforge-api.service
```

### Modified Files (4)

```
pineforge/live/config.py          # Added script_source field, mt5_backend, mt5_bridge_url
pineforge/live/bridge.py          # Connector-aware: uses MetaAPI or self-hosted bridge based on config
requirements.txt                   # Added FastAPI, SQLAlchemy, asyncpg, Alembic, JWT, bcrypt, httpx deps
.env.example                       # Added DATABASE_URL, JWT, APP, MT5_BACKEND, MT5_BRIDGE_URL
```

---

## Self-Hosted MT5 Bridge (Replaces MetaAPI)

### Why

MetaAPI charges ~$15-20/account/month. Their GitHub repos are client SDKs only (proprietary license). The server is closed-source. Self-hosting saves 85-90% at scale.

### Architecture

```
PineForge API
     │  MT5_BACKEND=bridge
     │  HTTP calls to MT5_BRIDGE_URL
     ▼
mt5bridge (FastAPI, port 5555)     ← runs in Docker
     │  MetaTrader5 Python package
     ▼
MT5 Terminal (Wine in Docker)
     │  Broker protocol
     ▼
Exness MT5 Server
```

### Components

```
mt5bridge/
├── app.py              # FastAPI server (lifespan, endpoints, auto-reconnect)
├── mt5_wrapper.py      # Thread-safe wrapper around MetaTrader5 package
├── schemas.py          # Pydantic request/response models
├── config.py           # Environment config (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
├── Dockerfile          # Debian + Wine + MT5 terminal + bridge server
├── docker-compose.yml  # Multi-account orchestration
├── entrypoint.sh       # Starts Xvfb + MT5 + bridge
├── requirements.txt    # fastapi, uvicorn, MetaTrader5, pydantic
└── README.md
```

### Connector Abstraction (`pineforge/live/connector.py`)

```python
MT5Connector (abstract base class)
├── MetaApiConnector   — wraps MetaAPI SDK (existing behavior)
└── BridgeConnector    — calls self-hosted bridge REST API via httpx
```

`LiveBridge.run()` checks `config.mt5_backend`:
- `"metaapi"` → uses MetaAPI SDK directly (unchanged)
- `"bridge"` → creates `BridgeConnector` + `ConnectorExecutor`

### Configuration

```env
# .env — switch backend
MT5_BACKEND=bridge                          # or "metaapi"
MT5_BRIDGE_URL=http://mt5bridge:5555        # self-hosted bridge URL
```

### Deployment

Requires x86-64 VPS (Wine doesn't run on ARM). Hetzner CX22 ~$5/mo.

```bash
cd mt5bridge
cp .env.example .env  # Set MT5 credentials
docker-compose up -d  # Start MT5 + bridge
```

### Bridge API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Connection status |
| POST | `/connect` | Login to MT5 `{login, password, server}` |
| GET | `/account` | Balance, equity, margin |
| POST | `/order/buy` | Market buy `{symbol, volume}` |
| POST | `/order/sell` | Market sell `{symbol, volume}` |
| POST | `/positions/close` | Close all `{symbol}` |
| GET | `/positions` | Open positions |
| POST | `/candles` | Historical OHLCV `{symbol, timeframe, count}` |

