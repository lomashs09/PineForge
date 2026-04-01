# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Runs MT5 terminal via Wine on Linux, exposes REST API for PineForge.
# Requires x86-64 host.
#
# Build:  docker build -t mt5bridge .
# Run:    docker run -e MT5_LOGIN=123 -e MT5_PASSWORD=xxx -e MT5_SERVER=Exness-MT5Trial6 -p 5555:5555 mt5bridge

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0

# ── System + Wine from default Ubuntu repos ───────────────────────────
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

# ── Download and install MetaTrader 5 ────────────────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -O /tmp/mt5setup.exe && \
    xvfb-run wine64 /tmp/mt5setup.exe /auto 2>/dev/null && \
    sleep 5 && \
    rm -f /tmp/mt5setup.exe && \
    echo "=== MT5 installed ===" && \
    find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1 || \
    echo "WARNING: terminal64.exe not found, MT5 may need manual install"

# ── Python environment ────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Bridge code ───────────────────────────────────────────────────────
COPY . /app/mt5bridge/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 5555

ENTRYPOINT ["/app/entrypoint.sh"]
