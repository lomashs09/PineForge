"""MT5 connector abstraction — switch between MetaAPI and self-hosted bridge.

Usage in LiveBridge:
    connector = create_connector(config)
    await connector.connect()
    result = await connector.buy(symbol, volume)
    positions = await connector.get_positions(symbol)
    candles = await connector.get_candles(symbol, timeframe, count)
    await connector.close_all(symbol)
    await connector.disconnect()
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("pineforge.live.connector")


@dataclass
class AccountInfo:
    balance: float = 0.0
    equity: float = 0.0
    currency: str = "USD"


@dataclass
class OrderResult:
    success: bool = False
    order_id: str = ""
    price: float = 0.0
    volume: float = 0.0
    error: str = ""


@dataclass
class PositionInfo:
    ticket: str = ""
    symbol: str = ""
    type: str = ""  # "buy"/"sell" or "POSITION_TYPE_BUY"/"POSITION_TYPE_SELL"
    volume: float = 0.0
    profit: float = 0.0
    price_open: float = 0.0


class MT5Connector(ABC):
    """Abstract interface for MT5 communication."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to MT5 terminal/service."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Clean up connection."""

    @abstractmethod
    async def get_account_info(self) -> Optional[AccountInfo]:
        """Get account balance, equity, currency."""

    @abstractmethod
    async def buy(self, symbol: str, volume: float) -> Optional[OrderResult]:
        """Place a market buy order."""

    @abstractmethod
    async def sell(self, symbol: str, volume: float) -> Optional[OrderResult]:
        """Place a market sell order."""

    @abstractmethod
    async def close_all(self, symbol: str) -> Tuple[bool, float]:
        """Close all positions for symbol. Returns (success, pnl)."""

    @abstractmethod
    async def get_positions(self, symbol: str = "") -> List[PositionInfo]:
        """Get open positions."""

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, count: int) -> List[Dict[str, Any]]:
        """Get historical OHLCV bars."""


# ── MetaAPI Implementation ────────────────────────────────────────────

class MetaApiConnector(MT5Connector):
    """Connects to MT5 via MetaAPI cloud service (existing behavior)."""

    def __init__(self, token: str, account_id: str):
        self._token = token
        self._account_id = account_id
        self._api = None
        self._account = None
        self._connection = None

    async def connect(self) -> None:
        from metaapi_cloud_sdk import MetaApi

        self._api = MetaApi(token=self._token)
        self._account = await self._api.metatrader_account_api.get_account(self._account_id)

        if self._account.state not in ("DEPLOYING", "DEPLOYED"):
            logger.info("Deploying MT5 account...")
            try:
                await self._account.deploy()
            except Exception as e:
                logger.info("Deploy note: %s", e)

        logger.info("Waiting for MT5 connection...")
        try:
            await self._account.wait_connected(timeout_in_seconds=60)
        except Exception:
            self._account = await self._api.metatrader_account_api.get_account(self._account_id)
            await self._account.wait_connected(timeout_in_seconds=120)

        self._connection = self._account.get_rpc_connection()
        await self._connection.connect()
        await self._connection.wait_synchronized(timeout_in_seconds=120)
        logger.info("Connected to MT5 via MetaAPI")

    async def disconnect(self) -> None:
        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass

    async def get_account_info(self) -> Optional[AccountInfo]:
        if not self._connection:
            return None
        try:
            info = await self._connection.get_account_information()
            return AccountInfo(
                balance=info.get("balance", 0),
                equity=info.get("equity", 0),
                currency=info.get("currency", "USD"),
            )
        except Exception as e:
            logger.error("get_account_info failed: %s", e)
            return None

    async def buy(self, symbol: str, volume: float) -> Optional[OrderResult]:
        if not self._connection:
            return None
        import asyncio
        try:
            result = await asyncio.wait_for(
                self._connection.create_market_buy_order(symbol, volume), timeout=30
            )
            return OrderResult(
                success=True,
                order_id=str(result.get("orderId", "")),
                price=result.get("price", result.get("openPrice", 0)),
                volume=volume,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def sell(self, symbol: str, volume: float) -> Optional[OrderResult]:
        if not self._connection:
            return None
        import asyncio
        try:
            result = await asyncio.wait_for(
                self._connection.create_market_sell_order(symbol, volume), timeout=30
            )
            return OrderResult(
                success=True,
                order_id=str(result.get("orderId", "")),
                price=result.get("price", result.get("openPrice", 0)),
                volume=volume,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def close_all(self, symbol: str) -> Tuple[bool, float]:
        if not self._connection:
            return False, 0.0
        import asyncio
        pnl = 0.0
        try:
            positions = await self.get_positions(symbol)
            for p in positions:
                pnl += p.profit
        except Exception:
            pass
        try:
            await asyncio.wait_for(
                self._connection.close_positions_by_symbol(symbol), timeout=30
            )
            return True, pnl
        except Exception as e:
            logger.error("close_all failed: %s", e)
            return False, pnl

    async def get_positions(self, symbol: str = "") -> List[PositionInfo]:
        if not self._connection:
            return []
        try:
            positions = await self._connection.get_positions()
            result = []
            for p in (positions or []):
                if symbol and p.get("symbol") != symbol:
                    continue
                result.append(PositionInfo(
                    ticket=str(p.get("id", "")),
                    symbol=p.get("symbol", ""),
                    type=p.get("type", ""),
                    volume=p.get("volume", 0),
                    profit=p.get("profit", 0),
                    price_open=p.get("openPrice", 0),
                ))
            return result
        except Exception:
            return []

    async def get_candles(self, symbol: str, timeframe: str, count: int) -> List[Dict[str, Any]]:
        if not self._account:
            return []
        try:
            bars = await self._account.get_historical_candles(symbol, timeframe, count)
            result = []
            for b in (bars or []):
                result.append({
                    "open": b.get("open", 0),
                    "high": b.get("high", 0),
                    "low": b.get("low", 0),
                    "close": b.get("close", 0),
                    "volume": b.get("tickVolume", b.get("volume", 0)),
                    "date": b.get("time", b.get("date")),
                })
            return sorted(result, key=lambda x: str(x.get("date", "")))
        except Exception as e:
            logger.error("get_candles failed: %s", e)
            return []


# ── Self-Hosted Bridge Implementation ─────────────────────────────────

class BridgeConnector(MT5Connector):
    """Connects to MT5 via self-hosted mt5bridge REST API."""

    def __init__(self, bridge_url: str, login: int = 0, password: str = "", server: str = ""):
        self._url = bridge_url.rstrip("/")
        self._login = login
        self._password = password
        self._server = server

    async def _request(self, method: str, path: str, json: dict = None) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(method, f"{self._url}{path}", json=json)
            resp.raise_for_status()
            return resp.json()

    async def connect(self) -> None:
        if self._login and self._password:
            await self._request("POST", "/connect", {
                "login": self._login,
                "password": self._password,
                "server": self._server,
            })
        # Verify connection
        health = await self._request("GET", "/health")
        if not health.get("connected"):
            raise ConnectionError("Bridge is not connected to MT5")
        logger.info("Connected to MT5 via bridge at %s", self._url)

    async def disconnect(self) -> None:
        try:
            await self._request("POST", "/disconnect")
        except Exception:
            pass

    async def get_account_info(self) -> Optional[AccountInfo]:
        try:
            data = await self._request("GET", "/account")
            return AccountInfo(
                balance=data.get("balance", 0),
                equity=data.get("equity", 0),
                currency=data.get("currency", "USD"),
            )
        except Exception as e:
            logger.error("get_account_info failed: %s", e)
            return None

    async def buy(self, symbol: str, volume: float) -> Optional[OrderResult]:
        try:
            data = await self._request("POST", "/order/buy", {"symbol": symbol, "volume": volume})
            return OrderResult(
                success=data.get("success", False),
                order_id=str(data.get("order_id", "")),
                price=data.get("price", 0),
                volume=data.get("volume", volume),
                error=data.get("retcode_desc", ""),
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def sell(self, symbol: str, volume: float) -> Optional[OrderResult]:
        try:
            data = await self._request("POST", "/order/sell", {"symbol": symbol, "volume": volume})
            return OrderResult(
                success=data.get("success", False),
                order_id=str(data.get("order_id", "")),
                price=data.get("price", 0),
                volume=data.get("volume", volume),
                error=data.get("retcode_desc", ""),
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def close_all(self, symbol: str) -> Tuple[bool, float]:
        try:
            data = await self._request("POST", "/positions/close", {"symbol": symbol})
            return data.get("success", False), data.get("price", 0)  # price carries PnL
        except Exception as e:
            logger.error("close_all failed: %s", e)
            return False, 0.0

    async def get_positions(self, symbol: str = "") -> List[PositionInfo]:
        try:
            params = f"?symbol={symbol}" if symbol else ""
            data = await self._request("GET", f"/positions{params}")
            return [
                PositionInfo(
                    ticket=str(p.get("ticket", "")),
                    symbol=p.get("symbol", ""),
                    type=p.get("type", ""),
                    volume=p.get("volume", 0),
                    profit=p.get("profit", 0),
                    price_open=p.get("price_open", 0),
                )
                for p in (data if isinstance(data, list) else [])
            ]
        except Exception:
            return []

    async def get_candles(self, symbol: str, timeframe: str, count: int) -> List[Dict[str, Any]]:
        try:
            data = await self._request("POST", "/candles", {
                "symbol": symbol, "timeframe": timeframe, "count": count,
            })
            return [
                {
                    "open": c.get("open", 0),
                    "high": c.get("high", 0),
                    "low": c.get("low", 0),
                    "close": c.get("close", 0),
                    "volume": c.get("volume", 0),
                    "date": c.get("time"),
                }
                for c in (data if isinstance(data, list) else [])
            ]
        except Exception as e:
            logger.error("get_candles failed: %s", e)
            return []


# ── Factory ───────────────────────────────────────────────────────────

def create_connector(
    backend: str = "metaapi",
    # MetaAPI args
    metaapi_token: str = "",
    metaapi_account_id: str = "",
    # Bridge args
    bridge_url: str = "",
    mt5_login: int = 0,
    mt5_password: str = "",
    mt5_server: str = "",
) -> MT5Connector:
    """Create the appropriate connector based on backend type.

    Args:
        backend: "metaapi" or "bridge"
        metaapi_token: MetaAPI API token (for metaapi backend)
        metaapi_account_id: MetaAPI account ID (for metaapi backend)
        bridge_url: URL of self-hosted bridge (for bridge backend), e.g. "http://localhost:5555"
        mt5_login: MT5 login number (for bridge backend)
        mt5_password: MT5 password (for bridge backend)
        mt5_server: MT5 server name (for bridge backend)
    """
    if backend == "bridge":
        if not bridge_url:
            raise ValueError("bridge_url is required for bridge backend")
        logger.info("Using self-hosted MT5 bridge at %s", bridge_url)
        return BridgeConnector(bridge_url, mt5_login, mt5_password, mt5_server)
    else:
        if not metaapi_token:
            raise ValueError("metaapi_token is required for metaapi backend")
        logger.info("Using MetaAPI cloud (account: %s)", metaapi_account_id)
        return MetaApiConnector(metaapi_token, metaapi_account_id)
