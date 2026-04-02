# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Based on gmag11/MetaTrader5-Docker which has a working MT5 install.
# Adds our FastAPI bridge server + RPyC + noVNC.

FROM gmag11/metatrader5:latest

ENV DISPLAY=:0 \
    WINEDEBUG=-all

USER root

# ── Additional packages ───────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        x11vnc novnc websockify \
        curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Install rpyc in Wine Python (gmag11 image already has MT5 + Python) ──
# gmag11 image has RPyC server on port 8001, we can use that directly
# But let's also ensure rpyc version compatibility
RUN wine /root/.wine/drive_c/Python312/python.exe -m pip install "rpyc>=5,<6" MetaTrader5 2>/dev/null || true

# ── Linux Python (FastAPI bridge) ─────────────────────────────────────
WORKDIR /app
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir \
        fastapi>=0.115.0 \
        "uvicorn[standard]>=0.34.0" \
        pydantic>=2.0.0 \
        mt5linux>=0.2.0 \
        "rpyc>=5,<6"

# ── Bridge code ───────────────────────────────────────────────────────
COPY . /app/mt5bridge/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 5555 6080

ENTRYPOINT ["/app/entrypoint.sh"]
