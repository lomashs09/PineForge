"""Bridge configuration from environment variables."""

from dataclasses import dataclass, field
import os


@dataclass
class BridgeConfig:
    # MT5 terminal
    mt5_path: str = ""  # Path to terminal64.exe (auto-detected if empty)

    # Account credentials (set via env or /connect endpoint)
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 5555

    # Behavior
    auto_connect: bool = True  # Connect to MT5 on startup if credentials are set
    reconnect_interval: int = 30  # Seconds between reconnect attempts
    request_timeout: int = 30  # Seconds for MT5 operations

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            mt5_path=os.getenv("MT5_PATH", ""),
            mt5_login=int(os.getenv("MT5_LOGIN", "0")),
            mt5_password=os.getenv("MT5_PASSWORD", ""),
            mt5_server=os.getenv("MT5_SERVER", ""),
            host=os.getenv("BRIDGE_HOST", "0.0.0.0"),
            port=int(os.getenv("BRIDGE_PORT", "5555")),
            auto_connect=os.getenv("BRIDGE_AUTO_CONNECT", "true").lower() == "true",
            reconnect_interval=int(os.getenv("BRIDGE_RECONNECT_INTERVAL", "30")),
            request_timeout=int(os.getenv("BRIDGE_REQUEST_TIMEOUT", "30")),
        )
