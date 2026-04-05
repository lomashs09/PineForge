"""Worker configuration from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class WorkerConfig:
    database_url: str = ""
    poll_interval: int = 5  # seconds between DB polls for start/stop requests
    max_bots: int = 50  # max concurrent bots on this worker
    worker_id: str = "worker-1"  # unique identifier for this worker instance
    use_subprocess: bool = False  # run each bot in its own process for crash isolation
    max_retries: int = 3  # max auto-restarts for crashed bots

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            poll_interval=int(os.getenv("WORKER_POLL_INTERVAL", "5")),
            max_bots=int(os.getenv("WORKER_MAX_BOTS", "50")),
            worker_id=os.getenv("WORKER_ID", "worker-1"),
            use_subprocess=os.getenv("WORKER_USE_SUBPROCESS", "").lower() in ("1", "true", "yes"),
            max_retries=int(os.getenv("WORKER_MAX_RETRIES", "3")),
        )
