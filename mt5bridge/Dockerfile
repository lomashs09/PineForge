# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Uses Wine 9+ (WineHQ) which implements the CRT functions needed by MT5/numpy.
# Ubuntu 22.04 default Wine 6 is too old (missing fetestexcept etc).

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0

# ── WineHQ stable (Wine 9+) ──────────────────────────────────────────
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common wget gnupg2 \
        xvfb xauth unzip cabextract curl \
        python3 python3-pip python3-venv && \
    # Add WineHQ repo
    mkdir -pm755 /etc/apt/keyrings && \
    wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key 2>/dev/null && \
    echo "deb [signed-by=/etc/apt/keyrings/winehq-archive.key] https://dl.winehq.org/wine-builds/ubuntu/ jammy main" > /etc/apt/sources.list.d/winehq.list && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-stable && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Verify wine version
    wine64 --version

# ── Wine init ─────────────────────────────────────────────────────────
RUN xvfb-run wineboot --init 2>/dev/null || true

# ── Portable Python 3.9 in Wine ──────────────────────────────────────
ENV WINEPY=/root/.wine/drive_c/Python/python.exe
RUN mkdir -p /root/.wine/drive_c/Python && \
    wget -q "https://www.python.org/ftp/python/3.9.13/python-3.9.13-embed-amd64.zip" -O /tmp/python.zip && \
    unzip -q /tmp/python.zip -d /root/.wine/drive_c/Python/ && \
    rm /tmp/python.zip && \
    sed -i 's/#import site/import site/' /root/.wine/drive_c/Python/python39._pth && \
    wget -q "https://bootstrap.pypa.io/get-pip.py" -O /tmp/get-pip.py && \
    wine64 $WINEPY /tmp/get-pip.py 2>/dev/null; \
    rm /tmp/get-pip.py && \
    echo "=== Wine Python 3.9 ready ==="

# ── Install MetaTrader5 + RPyC in Wine Python ────────────────────────
RUN wine64 $WINEPY -m pip install MetaTrader5 "rpyc>=5,<6" 2>/dev/null; \
    echo "=== MT5 + RPyC installed ==="

# ── Download and install MetaTrader 5 terminal ───────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -O /tmp/mt5setup.exe && \
    xvfb-run wine64 /tmp/mt5setup.exe /auto 2>/dev/null; \
    sleep 5 && \
    rm -f /tmp/mt5setup.exe && \
    echo "=== MT5 terminal ===" && \
    find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1 || true

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

EXPOSE 5555

ENTRYPOINT ["/app/entrypoint.sh"]
