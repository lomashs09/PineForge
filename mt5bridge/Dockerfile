# MT5 Bridge — Self-hosted MetaTrader 5 REST API
# Based on the proven gmag11/MetaTrader5-Docker approach:
# Wine + MT5 terminal + Python MetaTrader5 package + FastAPI server
#
# Build:  docker build -t mt5bridge .
# Run:    docker run -e MT5_LOGIN=123 -e MT5_PASSWORD=xxx -e MT5_SERVER=Exness-MT5Trial6 -p 5555:5555 mt5bridge
#
# Requires x86-64 host (Wine does not run on ARM).

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0

# ── System dependencies ──────────────────────────────────────────────
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        wine64 \
        wine32 \
        xvfb \
        wget \
        cabextract \
        python3 \
        python3-pip \
        python3-venv \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Wine setup ────────────────────────────────────────────────────────
RUN wineboot --init && \
    # Install winetricks for Visual C++ runtime (needed by MT5)
    wget -q https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks -O /usr/local/bin/winetricks && \
    chmod +x /usr/local/bin/winetricks && \
    xvfb-run winetricks -q vcrun2019 || true

# ── Download and install MetaTrader 5 ────────────────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -O /tmp/mt5setup.exe && \
    xvfb-run wine /tmp/mt5setup.exe /auto && \
    rm /tmp/mt5setup.exe && \
    # Find the terminal path
    find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1

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
