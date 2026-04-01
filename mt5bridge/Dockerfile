# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Architecture:
#   Linux Python (FastAPI server) → RPyC → Wine Python (MetaTrader5 package) → MT5 Terminal
#
# The MetaTrader5 pip package only works on Windows. We use mt5linux (RPyC bridge)
# to call it from Linux Python through Wine.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0

# ── System + Wine ─────────────────────────────────────────────────────
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        wine64 \
        wine32 \
        xvfb \
        xauth \
        wget \
        cabextract \
        curl \
        python3 \
        python3-pip \
        python3-venv \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Wine init ─────────────────────────────────────────────────────────
RUN xvfb-run wine64 wineboot --init 2>/dev/null || true

# ── Install Python inside Wine (for MetaTrader5 package) ─────────────
RUN wget -q "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe" -O /tmp/python-setup.exe && \
    xvfb-run wine64 /tmp/python-setup.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 2>/dev/null && \
    sleep 3 && \
    rm -f /tmp/python-setup.exe && \
    echo "=== Wine Python installed ===" && \
    wine64 python --version 2>/dev/null || true

# ── Install MetaTrader5 + RPyC in Wine Python ────────────────────────
RUN wine64 python -m pip install --upgrade pip 2>/dev/null && \
    wine64 python -m pip install MetaTrader5 rpyc 2>/dev/null && \
    echo "=== MT5 Python package installed in Wine ==="

# ── Download and install MetaTrader 5 terminal ───────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -O /tmp/mt5setup.exe && \
    xvfb-run wine64 /tmp/mt5setup.exe /auto 2>/dev/null && \
    sleep 5 && \
    rm -f /tmp/mt5setup.exe && \
    echo "=== MT5 terminal installed ===" && \
    find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1 || true

# ── Linux Python environment (FastAPI server + mt5linux RPyC client) ──
WORKDIR /app
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir \
        fastapi>=0.115.0 \
        uvicorn[standard]>=0.34.0 \
        pydantic>=2.0.0 \
        mt5linux>=0.2.0 \
        rpyc>=6.0.0

# ── Bridge code ───────────────────────────────────────────────────────
COPY . /app/mt5bridge/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 5555

ENTRYPOINT ["/app/entrypoint.sh"]
