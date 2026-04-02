# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Wine 11 (WineHQ) + noVNC + MT5 installer pre-downloaded.
# MT5 terminal installs on first boot via VNC.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=;dbghelp=d" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0 \
    WINEDEBUG=-all

# ── WineHQ stable + VNC ──────────────────────────────────────────────
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common wget gnupg2 \
        xvfb xauth unzip cabextract curl \
        x11vnc novnc websockify openbox \
        python3 python3-pip python3-venv && \
    mkdir -pm755 /etc/apt/keyrings && \
    wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key 2>/dev/null && \
    echo "deb [signed-by=/etc/apt/keyrings/winehq-archive.key] https://dl.winehq.org/wine-builds/ubuntu/ jammy main" > /etc/apt/sources.list.d/winehq.list && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-stable && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    wine --version

# ── Wine init + disable debugger ──────────────────────────────────────
RUN Xvfb :0 -screen 0 1280x720x24 & sleep 1 && \
    wineboot --init 2>/dev/null; sleep 5 && \
    # Disable AeDebug so MT5 doesn't detect a debugger
    wine reg add "HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug" /v Debugger /t REG_SZ /d "" /f 2>/dev/null || true && \
    wine reg add "HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug" /v Auto /t REG_SZ /d "0" /f 2>/dev/null || true && \
    # Remove winedbg binary
    find /opt -name "winedbg" -delete 2>/dev/null || true && \
    kill %1 2>/dev/null || true

# ── Portable Python 3.9 in Wine ──────────────────────────────────────
ENV WINEPY=/root/.wine/drive_c/Python/python.exe
RUN mkdir -p /root/.wine/drive_c/Python && \
    wget -q "https://www.python.org/ftp/python/3.9.13/python-3.9.13-embed-amd64.zip" -O /tmp/python.zip && \
    unzip -q /tmp/python.zip -d /root/.wine/drive_c/Python/ && \
    rm /tmp/python.zip && \
    sed -i 's/#import site/import site/' /root/.wine/drive_c/Python/python39._pth && \
    wget -q "https://bootstrap.pypa.io/get-pip.py" -O /tmp/get-pip.py && \
    wine $WINEPY /tmp/get-pip.py 2>/dev/null; \
    rm /tmp/get-pip.py

# ── MetaTrader5 + RPyC in Wine Python ────────────────────────────────
RUN wine $WINEPY -m pip install MetaTrader5 "rpyc>=5,<6" 2>/dev/null; \
    echo "=== MT5 + RPyC installed ==="

# ── Download MT5 installer ───────────────────────────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" \
    -O /root/.wine/drive_c/mt5setup.exe

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
