"""MetaAPI Connection Manager — persistent, shared connections.

Instead of deploying/undeploying MetaAPI accounts on every bot start/stop,
we maintain persistent connections that multiple bots share.

Benefits:
- One deployment per account (not per bot start)
- Bots reuse existing connections instantly
- Keepalive pings prevent MetaAPI auto-undeploy
- Auto-reconnect on disconnect

MetaAPI charges per deployment (6-hour minimum). This reduces costs by 80-90%
at scale by minimizing deploy/undeploy cycles.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("pineforge.connection_manager")


class ManagedConnection:
    """A single persistent MetaAPI account connection."""

    def __init__(self, metaapi_account_id: str, metaapi_token: str):
        self.account_id = metaapi_account_id
        self.token = metaapi_token
        self.account = None
        self.connection = None
        self.connected = False
        self.bot_count = 0  # How many bots are using this connection
        self.last_used = datetime.now(timezone.utc)
        self.deploy_count = 0

    async def ensure_connected(self) -> None:
        """Deploy and connect if not already connected."""
        from metaapi_cloud_sdk import MetaApi

        self.last_used = datetime.now(timezone.utc)

        if self.connected and self.connection:
            # Verify still connected
            try:
                self.account = await MetaApi(token=self.token).metatrader_account_api.get_account(self.account_id)
                if self.account.state == "DEPLOYED" and self.account.connection_status == "CONNECTED":
                    return  # Still good
            except Exception:
                pass
            self.connected = False

        logger.info("Connecting account %s...", self.account_id)
        api = MetaApi(token=self.token)
        self.account = await api.metatrader_account_api.get_account(self.account_id)

        if self.account.state not in ("DEPLOYING", "DEPLOYED"):
            logger.info("Deploying account %s...", self.account_id)
            await self.account.deploy()
            self.deploy_count += 1

        await self.account.wait_connected(timeout_in_seconds=120)

        self.connection = self.account.get_rpc_connection()
        await self.connection.connect()
        await self.connection.wait_synchronized(timeout_in_seconds=120)

        self.connected = True
        logger.info("Account %s connected (deploys: %d)", self.account_id, self.deploy_count)

    async def disconnect(self) -> None:
        """Close connection and undeploy."""
        self.connected = False
        if self.connection:
            try:
                await self.connection.close()
            except Exception:
                pass
            self.connection = None

        if self.account and self.account.state in ("DEPLOYING", "DEPLOYED"):
            try:
                await self.account.undeploy()
                logger.info("Undeployed account %s", self.account_id)
            except Exception as e:
                logger.warning("Failed to undeploy %s: %s", self.account_id, e)

    def acquire(self) -> None:
        """A bot is using this connection."""
        self.bot_count += 1
        self.last_used = datetime.now(timezone.utc)

    def release(self) -> None:
        """A bot stopped using this connection."""
        self.bot_count = max(0, self.bot_count - 1)
        self.last_used = datetime.now(timezone.utc)


class ConnectionManager:
    """Manages persistent MetaAPI connections shared across bots.

    Usage:
        mgr = ConnectionManager(metaapi_token)
        conn = await mgr.get_connection(metaapi_account_id)
        # conn.connection is the RPC connection
        # conn.account is the MetaAPI account
        mgr.release_connection(metaapi_account_id)
    """

    def __init__(self, metaapi_token: str):
        self._token = metaapi_token
        self._connections: Dict[str, ManagedConnection] = {}
        self._lock = asyncio.Lock()
        self._keepalive_task: Optional[asyncio.Task] = None

    def start_keepalive(self) -> None:
        """Start background keepalive pings."""
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Ping active connections every 5 minutes to prevent MetaAPI auto-undeploy."""
        try:
            while True:
                await asyncio.sleep(300)  # 5 minutes
                for account_id, conn in list(self._connections.items()):
                    if conn.connected and conn.bot_count > 0:
                        try:
                            # Simple ping — get account info
                            if conn.connection:
                                await conn.connection.get_account_information()
                                logger.debug("Keepalive ping: %s OK", account_id)
                        except Exception as e:
                            logger.warning("Keepalive failed for %s: %s — will reconnect on next use", account_id, e)
                            conn.connected = False
        except asyncio.CancelledError:
            pass

    async def get_connection(self, metaapi_account_id: str) -> ManagedConnection:
        """Get or create a persistent connection for an account."""
        async with self._lock:
            if metaapi_account_id not in self._connections:
                self._connections[metaapi_account_id] = ManagedConnection(
                    metaapi_account_id, self._token
                )

            conn = self._connections[metaapi_account_id]

        await conn.ensure_connected()
        conn.acquire()
        return conn

    def release_connection(self, metaapi_account_id: str) -> None:
        """Release a bot's hold on a connection (keeps the connection alive)."""
        conn = self._connections.get(metaapi_account_id)
        if conn:
            conn.release()
            logger.info("Released connection %s (remaining bots: %d)",
                        metaapi_account_id, conn.bot_count)

    async def remove_connection(self, metaapi_account_id: str) -> None:
        """Fully disconnect and undeploy (only when user removes broker account)."""
        conn = self._connections.pop(metaapi_account_id, None)
        if conn:
            await conn.disconnect()

    async def shutdown(self) -> None:
        """Shut down all connections (server shutdown)."""
        if self._keepalive_task:
            self._keepalive_task.cancel()

        # Only undeploy accounts with 0 active bots
        for account_id, conn in list(self._connections.items()):
            if conn.bot_count == 0:
                await conn.disconnect()
            else:
                logger.info("Keeping %s deployed (has %d active bots)", account_id, conn.bot_count)

        self._connections.clear()

    def get_stats(self) -> dict:
        """Return connection pool stats."""
        return {
            "total_connections": len(self._connections),
            "active_connections": sum(1 for c in self._connections.values() if c.connected),
            "total_bots": sum(c.bot_count for c in self._connections.values()),
            "total_deploys": sum(c.deploy_count for c in self._connections.values()),
            "connections": {
                aid: {
                    "connected": c.connected,
                    "bots": c.bot_count,
                    "deploys": c.deploy_count,
                    "last_used": c.last_used.isoformat(),
                }
                for aid, c in self._connections.items()
            },
        }
