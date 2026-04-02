# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# FIXES APPLIED:
# 1. numpy<2 — avoids fetestexcept CRT call that Wine doesn't implement
# 2. vcrun2022 via winetricks — provides real MS CRT + suppresses debugger detection
# 3. dbghelp=d override — disables debug helper DLL that MT5 detects
# 4. openbox WM + noVNC — for manual MT5 install via browser if needed

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=;dbghelp=d" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0 \
    WINEDEBUG=-all

# ── WineHQ 11 + VNC + window manager ─────────────────────────────────
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common wget gnupg2 \
        xvfb xauth unzip cabextract curl \
        x11vnc novnc websockify openbox xterm \
        python3 python3-pip python3-venv && \
    mkdir -pm755 /etc/apt/keyrings && \
    wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key 2>/dev/null && \
    echo "deb [signed-by=/etc/apt/keyrings/winehq-archive.key] https://dl.winehq.org/wine-builds/ubuntu/ jammy main" > /etc/apt/sources.list.d/winehq.list && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-stable && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    wine --version

# ── Wine init + winetricks vcrun2022 ─────────────────────────────────
# vcrun2022 provides real Microsoft CRT DLLs (fetestexcept etc) and
# replaces debug helpers that MT5 installer detects.
RUN Xvfb :0 -screen 0 1280x720x24 & sleep 1 && \
    wineboot --init 2>/dev/null; sleep 5 && \
    wget -q https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks -O /usr/local/bin/winetricks && \
    chmod +x /usr/local/bin/winetricks && \
    winetricks -q vcrun2022 2>/dev/null; \
    # Disable AeDebug registry
    wine reg add "HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug" /v Debugger /t REG_SZ /d "" /f 2>/dev/null; \
    wine reg add "HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug" /v Auto /t REG_SZ /d "0" /f 2>/dev/null; \
    # Delete winedbg binary
    find /opt -name "winedbg" -delete 2>/dev/null; \
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

# ── MetaTrader5 + numpy<2 + RPyC in Wine Python ─────────────────────
# KEY FIX: numpy<2 avoids fetestexcept which Wine doesn't implement
RUN wine $WINEPY -m pip install "numpy<2" MetaTrader5 "rpyc>=5,<6" 2>/dev/null; \
    echo "=== Packages installed ==="

# ── MT5 terminal (pre-built from Mac, bypasses installer debugger check) ─
# The mt5setup.exe installer refuses to run under Wine (debugger detection).
# Instead, we copy the terminal files from a real MT5 installation.
# To regenerate: install MT5 on Mac/Windows, tar the folder, place here.
COPY mt5-terminal.tar.gz /tmp/mt5-terminal.tar.gz
RUN mkdir -p "/root/.wine/drive_c/Program Files/MetaTrader 5" && \
    cd "/root/.wine/drive_c/Program Files/MetaTrader 5" && \
    tar xzf /tmp/mt5-terminal.tar.gz && \
    rm /tmp/mt5-terminal.tar.gz && \
    echo "=== MT5 terminal installed ===" && \
    ls -la terminal64.exe

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
