"""Manages multiple MT5 terminal instances — one per broker account.

Each account gets its own MT5 installation directory and terminal process.
The MetaTrader5 Python package connects to a specific terminal by path.

Directory structure on Windows:
  C:\MT5\
  ├── Acc_413471385\terminal64.exe  → logged into 413471385@Exness-MT5Trial6
  ├── Acc_433415353\terminal64.exe  → logged into 433415353@Exness-MT5Trial7
  └── template\                     → clean MT5 install to copy from
"""

import asyncio
import logging
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("worker.accounts")

MT5_BASE_DIR = Path(os.getenv("MT5_BASE_DIR", r"C:\MT5"))
MT5_TEMPLATE_DIR = MT5_BASE_DIR / "template"

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mt5acct")


class MT5Instance:
    """A single MT5 terminal instance for one broker account."""

    def __init__(self, mt5_login: str, mt5_password: str, mt5_server: str):
        self.login = mt5_login
        self.password = mt5_password
        self.server = mt5_server
        self.dir = MT5_BASE_DIR / f"Acc_{mt5_login}"
        self.terminal_path = self.dir / "terminal64.exe"
        self.connected = False
        self.process: Optional[subprocess.Popen] = None

    def _ensure_installed(self) -> bool:
        """Copy MT5 from template if not already installed for this account."""
        if self.terminal_path.exists():
            return True

        if not MT5_TEMPLATE_DIR.exists() or not (MT5_TEMPLATE_DIR / "terminal64.exe").exists():
            logger.error("MT5 template not found at %s", MT5_TEMPLATE_DIR)
            logger.error("Please copy your MT5 installation to %s", MT5_TEMPLATE_DIR)
            return False

        logger.info("Copying MT5 for account %s...", self.login)
        self.dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(MT5_TEMPLATE_DIR, self.dir, dirs_exist_ok=True)
        logger.info("MT5 copied to %s", self.dir)
        return True

    def _start_terminal(self):
        """Start the MT5 terminal process."""
        if self.process and self.process.poll() is None:
            return  # Already running

        logger.info("Starting MT5 terminal for %s@%s", self.login, self.server)
        self.process = subprocess.Popen(
            [str(self.terminal_path), "/portable"],
            cwd=str(self.dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(15)  # First launch needs time to download broker data

    def _initialize_and_login(self) -> bool:
        """Initialize MT5 package and login to this terminal.

        Retries initialization up to 3 times with increasing delays,
        since the terminal may still be loading on first launch.
        """
        import MetaTrader5 as mt5

        # Retry initialization — terminal may need more time on first start
        for attempt in range(3):
            if mt5.initialize(path=str(self.terminal_path)):
                break
            err = mt5.last_error()
            logger.warning("MT5 initialize attempt %d/3 failed for %s: %s",
                           attempt + 1, self.login, err)
            if attempt < 2:
                wait = 15 * (attempt + 1)
                logger.info("Waiting %ds before retry...", wait)
                time.sleep(wait)
        else:
            logger.error("MT5 initialize failed for %s after 3 attempts", self.login)
            return False

        if not mt5.login(login=int(self.login), password=self.password, server=self.server):
            err = mt5.last_error()
            logger.error("MT5 login failed for %s@%s: %s", self.login, self.server, err)
            return False

        info = mt5.account_info()
        logger.info("Logged in: %s (%d) balance=%.2f %s",
                     info.name, info.login, info.balance, info.currency)
        self.connected = True
        return True

    def _shutdown(self):
        """Shutdown MT5 connection (not the terminal process)."""
        import MetaTrader5 as mt5
        mt5.shutdown()
        self.connected = False

    def stop_terminal(self):
        """Kill the terminal process."""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.connected = False


class AccountManager:
    """Manages multiple MT5 terminal instances for different broker accounts."""

    def __init__(self):
        self._instances: Dict[str, MT5Instance] = {}  # login → instance
        self._lock = asyncio.Lock()

    async def ensure_account_ready(self, mt5_login: str, mt5_password: str, mt5_server: str) -> MT5Instance:
        """Ensure an MT5 instance is running and logged in for this account."""
        async with self._lock:
            if mt5_login in self._instances and self._instances[mt5_login].connected:
                return self._instances[mt5_login]

            instance = MT5Instance(mt5_login, mt5_password, mt5_server)
            loop = asyncio.get_event_loop()

            # Install, start, and login — all in thread (blocking operations)
            ok = await loop.run_in_executor(_executor, self._setup_instance, instance)
            if not ok:
                raise RuntimeError(f"Failed to setup MT5 for {mt5_login}@{mt5_server}")

            self._instances[mt5_login] = instance
            return instance

    @staticmethod
    def _setup_instance(instance: MT5Instance) -> bool:
        """Setup MT5 instance (runs in thread)."""
        if not instance._ensure_installed():
            return False
        instance._start_terminal()
        return instance._initialize_and_login()

    async def get_instance(self, mt5_login: str) -> Optional[MT5Instance]:
        """Get a running instance by login."""
        return self._instances.get(mt5_login)

    async def shutdown_all(self):
        """Stop all terminal instances."""
        for login, instance in self._instances.items():
            logger.info("Shutting down MT5 for %s", login)
            instance.stop_terminal()
        self._instances.clear()
