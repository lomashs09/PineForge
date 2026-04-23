"""Bot logging utilities — captures print() output and routes to database."""

import asyncio
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models.bot_log import BotLog
from ..models.bot_trade import BotTrade

# Patterns for parsing trade execution lines from LiveBridge/Executor print output
# Format: [LIVE] BUY 0.01 XAUUSDm @ 4520.50 -> order #12345
#   also: [LIVE] BUY 0.01 XAUUSDm @  -> order #12345  (empty price from MetaAPI)
_TRADE_BUY_RE = re.compile(
    r"\[(?:LIVE|DRY RUN)\]\s+BUY\s+([\d.]+)\s+(\S+)\s*(?:@\s*([\d.]+)?)?\s*->\s*order\s*#?(\S+)",
    re.IGNORECASE,
)
_TRADE_SELL_RE = re.compile(
    r"\[(?:LIVE|DRY RUN)\]\s+SELL\s+([\d.]+)\s+(\S+)\s*(?:@\s*([\d.]+)?)?\s*->\s*order\s*#?(\S+)",
    re.IGNORECASE,
)
# Format: [LIVE] Closed all XAUUSDm positions pnl=-1.23
#   also: [LIVE] Closed 1/1 XAUUSDm positions pnl=-1.23  (per-bot close)
_TRADE_CLOSE_RE = re.compile(
    r"\[(?:LIVE|DRY RUN)\]\s+Closed (?:all|\d+/\d+)\s+(\S+)\s+positions\s*(?:pnl=([-\d.]+))?",
    re.IGNORECASE,
)
_TRADE_WOULD_BUY_RE = re.compile(
    r"\[DRY RUN\]\s+Would BUY\s+([\d.]+)\s+(?:lots of\s+)?(\S+)",
    re.IGNORECASE,
)
_TRADE_WOULD_SELL_RE = re.compile(
    r"\[DRY RUN\]\s+Would SELL\s+([\d.]+)\s+(?:lots of\s+)?(\S+)",
    re.IGNORECASE,
)


class BotDatabaseHandler(logging.Handler):
    """Logging handler that batches log records and writes them to the bot_logs table.
    Also detects trade execution lines and writes to bot_trades table.
    """

    def __init__(
        self,
        bot_id: uuid.UUID,
        session_factory: async_sessionmaker,
        broker_account_id: uuid.UUID = None,
        flush_interval: float = 1.0,
        batch_size: int = 50,
    ):
        super().__init__()
        self.bot_id = bot_id
        self.broker_account_id = broker_account_id
        self.session_factory = session_factory
        self._queue: asyncio.Queue = asyncio.Queue()
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Start the background consumer that drains the queue."""
        self._task = asyncio.create_task(self._consumer())

    async def stop(self):
        """Flush remaining records and stop the consumer."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final flush
        await self._flush_all()

    def emit(self, record: logging.LogRecord):
        """Put the log record into the async queue (thread-safe for sync callers)."""
        level = getattr(record, "bot_level", self._map_level(record.levelno))
        metadata = getattr(record, "bot_metadata", None)
        message = self.format(record) if self.formatter else record.getMessage()

        entry = {
            "bot_id": self.bot_id,
            "level": level,
            "message": message,
            "metadata_": metadata,
            "created_at": datetime.now(timezone.utc),
        }

        try:
            self._queue.put_nowait(("log", entry))
        except asyncio.QueueFull:
            pass  # Drop under backpressure — queue consumer will catch up

        # Check if this is a trade execution line and queue a trade record
        trade = self._parse_trade(message)
        if trade:
            try:
                self._queue.put_nowait(("trade", trade))
            except asyncio.QueueFull:
                pass

    def _parse_trade(self, message: str) -> Optional[dict]:
        """Try to parse a trade execution from a log message."""
        now = datetime.now(timezone.utc)

        m = _TRADE_BUY_RE.search(message)
        if m:
            price = float(m.group(3)) if m.group(3) else 0
            return {
                "bot_id": self.bot_id,
                "broker_account_id": self.broker_account_id,
                "direction": "long",
                "symbol": m.group(2),
                "lot_size": float(m.group(1)),
                "entry_price": price,
                "signal": "entry_long",
                "order_id": m.group(4),
                "opened_at": now,
            }

        m = _TRADE_SELL_RE.search(message)
        if m:
            price = float(m.group(3)) if m.group(3) else 0
            return {
                "bot_id": self.bot_id,
                "broker_account_id": self.broker_account_id,
                "direction": "short",
                "symbol": m.group(2),
                "lot_size": float(m.group(1)),
                "entry_price": price,
                "signal": "entry_short",
                "order_id": m.group(4),
                "opened_at": now,
            }

        m = _TRADE_WOULD_BUY_RE.search(message)
        if m:
            return {
                "bot_id": self.bot_id,
                "broker_account_id": self.broker_account_id,
                "direction": "long",
                "symbol": m.group(2),
                "lot_size": float(m.group(1)),
                "entry_price": 0,
                "signal": "entry_long",
                "order_id": "dry-run",
                "opened_at": now,
            }

        m = _TRADE_WOULD_SELL_RE.search(message)
        if m:
            return {
                "bot_id": self.bot_id,
                "broker_account_id": self.broker_account_id,
                "direction": "short",
                "symbol": m.group(2),
                "lot_size": float(m.group(1)),
                "entry_price": 0,
                "signal": "entry_short",
                "order_id": "dry-run",
                "opened_at": now,
            }

        m = _TRADE_CLOSE_RE.search(message)
        if m:
            pnl = float(m.group(2)) if m.group(2) else None
            return {
                "bot_id": self.bot_id,
                "broker_account_id": self.broker_account_id,
                "direction": "long",
                "symbol": m.group(1),
                "lot_size": 0,
                "entry_price": 0,
                "signal": "close",
                "order_id": "close-all",
                "pnl": pnl,
                "opened_at": now,
                "closed_at": now,
            }

        return None

    @staticmethod
    def _map_level(levelno: int) -> str:
        if levelno >= logging.ERROR:
            return "error"
        if levelno >= logging.WARNING:
            return "warning"
        return "info"

    async def _consumer(self):
        """Background task that batch-inserts log entries."""
        try:
            while True:
                await asyncio.sleep(self._flush_interval)
                try:
                    await self._flush_all()
                except Exception as exc:
                    logging.getLogger(__name__).warning("Bot logger DB flush failed: %s", exc)
        except asyncio.CancelledError:
            try:
                await self._flush_all()
            except Exception as exc:
                logging.getLogger(__name__).warning("Bot logger DB flush failed: %s", exc)

    async def _flush_all(self):
        """Drain the queue and insert all pending entries."""
        logs = []
        trades = []
        while not self._queue.empty():
            try:
                kind, entry = self._queue.get_nowait()
                if kind == "log":
                    logs.append(entry)
                elif kind == "trade":
                    trades.append(entry)
            except asyncio.QueueEmpty:
                break

        if not logs and not trades:
            return

        try:
            async with self.session_factory() as session:
                for entry in logs:
                    session.add(BotLog(**entry))
                for entry in trades:
                    if entry.get("broker_account_id"):
                        session.add(BotTrade(**entry))
                await session.commit()
        except Exception:
            pass  # Don't crash the bot if logging fails


# Patterns for detecting log levels from print() output
_LEVEL_TRADE_PATTERN = re.compile(r"\[(?:LIVE|DRY RUN)\]\s+(?:BUY|SELL|Would BUY|Would SELL|Close|No \S+ positions)", re.IGNORECASE)
_LEVEL_SIGNAL_PATTERN = re.compile(r"Signal queued|Executing queued signal|Flipping:", re.IGNORECASE)
_LEVEL_HEARTBEAT_PATTERN = re.compile(r"HEARTBEAT", re.IGNORECASE)
_LEVEL_ERROR_PATTERN = re.compile(r"\[ERROR\]|Error:|Risk blocked:", re.IGNORECASE)
_LEVEL_NEW_BAR_PATTERN = re.compile(r"New bar:")


class BotPrintCapture(io.TextIOBase):
    """A writable stream that captures print() output and routes it to a logger.

    Detects log levels from output patterns (trade, signal, heartbeat, error).
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            level, bot_level = self._detect_level(line)
            record = self._logger.makeRecord(
                self._logger.name, level, "", 0, line, (), None
            )
            record.bot_level = bot_level
            self._logger.handle(record)
        return len(text)

    def flush(self):
        if self._buffer.strip():
            line = self._buffer.strip()
            self._buffer = ""
            level, bot_level = self._detect_level(line)
            record = self._logger.makeRecord(
                self._logger.name, level, "", 0, line, (), None
            )
            record.bot_level = bot_level
            self._logger.handle(record)

    @staticmethod
    def _detect_level(line: str) -> tuple:
        """Returns (logging_level, bot_level_string)."""
        if _LEVEL_TRADE_PATTERN.search(line):
            return logging.INFO, "trade"
        if _LEVEL_SIGNAL_PATTERN.search(line):
            return logging.INFO, "signal"
        if _LEVEL_HEARTBEAT_PATTERN.search(line):
            return logging.INFO, "heartbeat"
        if _LEVEL_ERROR_PATTERN.search(line):
            return logging.ERROR, "error"
        if _LEVEL_NEW_BAR_PATTERN.search(line):
            return logging.INFO, "info"
        return logging.INFO, "info"

    @property
    def writable(self) -> bool:
        return True
