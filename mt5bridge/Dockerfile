# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Architecture:
#   Linux Python (FastAPI) → RPyC → Wine Python (MetaTrader5) → MT5 Terminal
#
# Uses Python embed (zip, no installer) inside Wine prefix.

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
        unzip \
        cabextract \
        curl \
        python3 \
        python3-pip \
        python3-venv \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Wine init ─────────────────────────────────────────────────────────
RUN xvfb-run wine64 wineboot --init 2>/dev/null || true

# ── Portable Python in Wine (no installer needed) ─────────────────────
RUN mkdir -p /root/.wine/drive_c/Python312 && \
    wget -q "https://www.python.org/ftp/python/3.12.3/python-3.12.3-embed-amd64.zip" -O /tmp/python.zip && \
    unzip -q /tmp/python.zip -d /root/.wine/drive_c/Python312/ && \
    rm /tmp/python.zip && \
    # Enable pip by uncommenting import site in python312._pth
    sed -i 's/#import site/import site/' /root/.wine/drive_c/Python312/python312._pth && \
    # Install pip
    wget -q "https://bootstrap.pypa.io/get-pip.py" -O /tmp/get-pip.py && \
    wine64 /root/.wine/drive_c/Python312/python.exe /tmp/get-pip.py 2>/dev/null && \
    rm /tmp/get-pip.py && \
    echo "=== Wine Python ready ==="

# Shortcut for wine python
ENV WINE_PYTHON="wine64 /root/.wine/drive_c/Python312/python.exe"

# ── Install MetaTrader5 + RPyC in Wine Python ────────────────────────
RUN $WINE_PYTHON -m pip install MetaTrader5 rpyc 2>/dev/null && \
    echo "=== MT5 + RPyC installed in Wine Python ==="

# ── Download and install MetaTrader 5 terminal ───────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -O /tmp/mt5setup.exe && \
    xvfb-run wine64 /tmp/mt5setup.exe /auto 2>/dev/null; \
    sleep 5 && \
    rm -f /tmp/mt5setup.exe && \
    echo "=== MT5 terminal ===" && \
    find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1 || true

# ── Linux Python (FastAPI bridge server) ──────────────────────────────
WORKDIR /app
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir \
        fastapi>=0.115.0 \
        "uvicorn[standard]>=0.34.0" \
        pydantic>=2.0.0 \
        mt5linux>=0.2.0 \
        rpyc>=6.0.0

# ── Bridge code ───────────────────────────────────────────────────────
COPY . /app/mt5bridge/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 5555

ENTRYPOINT ["/app/entrypoint.sh"]
