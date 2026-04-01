# MT5 Bridge — Self-hosted MetaTrader 5 REST API
#
# Based on proven approaches from gmag11/MetaTrader5-Docker and
# ejtraderLabs/Metatrader5-Docker.
#
# Build:  docker build -t mt5bridge .
# Run:    docker run -e MT5_LOGIN=123 -e MT5_PASSWORD=xxx -e MT5_SERVER=Exness-MT5Trial6 -p 5555:5555 mt5bridge
#
# Requires x86-64 host (Wine does not run on ARM).

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WINEPREFIX=/root/.wine \
    WINEARCH=win64 \
    DISPLAY=:0

# ── System + Wine repo ────────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common \
        wget \
        gnupg2 \
        curl \
        xvfb \
        xauth \
        cabextract \
        python3 \
        python3-pip \
        python3-venv \
        && \
    # Add WineHQ repo for latest stable Wine
    dpkg --add-architecture i386 && \
    mkdir -pm755 /etc/apt/keyrings && \
    wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key && \
    wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/ubuntu/dists/jammy/winehq-jammy.sources && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-stable && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Wine init ─────────────────────────────────────────────────────────
RUN xvfb-run wineboot --init && \
    # Install Visual C++ runtime (needed by MT5)
    wget -q https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks -O /usr/local/bin/winetricks && \
    chmod +x /usr/local/bin/winetricks && \
    xvfb-run winetricks -q vcrun2019 || true

# ── Download and install MetaTrader 5 ────────────────────────────────
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -O /tmp/mt5setup.exe && \
    xvfb-run wine64 /tmp/mt5setup.exe /auto && \
    sleep 5 && \
    rm -f /tmp/mt5setup.exe && \
    echo "MT5 installed at:" && \
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
