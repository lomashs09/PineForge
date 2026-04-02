#!/bin/bash
set -e

export WINEDEBUG=-all
export DISPLAY=:0

echo "=== MT5 Bridge Starting ==="
echo "Login: ${MT5_LOGIN:-not set}"
echo "Server: ${MT5_SERVER:-not set}"
echo "Port: ${BRIDGE_PORT:-5555}"

# ── Virtual display ───────────────────────────────────────────────────
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
Xvfb :0 -screen 0 1280x720x24 &
sleep 2

# ── VNC (browser access on port 6080) ────────────────────────────────
x11vnc -display :0 -forever -nopw -rfbport 5900 -bg -q 2>/dev/null || true
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
sleep 1
echo "VNC: http://$(hostname -I | awk '{print $1}'):6080/vnc.html"

# ── MT5 terminal ─────────────────────────────────────────────────────
MT5_EXE=$(find /root/.wine /home -name "terminal64.exe" 2>/dev/null | head -1)
if [ -n "$MT5_EXE" ]; then
    echo "Starting MT5: $MT5_EXE"
    wine "$MT5_EXE" /portable &
    sleep 10
    export MT5_PATH="$MT5_EXE"
else
    echo "ERROR: MT5 terminal not found in this image!"
    find /root/.wine -name "*.exe" 2>/dev/null | head -10
fi

# ── RPyC server ───────────────────────────────────────────────────────
# gmag11 image has Python at different paths — find it
WINE_PY=$(find /root/.wine -name "python.exe" -path "*/Python*/python.exe" 2>/dev/null | head -1)
if [ -z "$WINE_PY" ]; then
    echo "ERROR: Wine Python not found!"
    exit 1
fi
echo "Wine Python: $WINE_PY"

echo "Starting RPyC server..."
wine "$WINE_PY" -c "
from rpyc.utils.server import ThreadedServer
from rpyc import SlaveService
t = ThreadedServer(SlaveService, port=18812, protocol_config={'allow_public_attrs': True, 'allow_all_attrs': True})
t.start()
" &
RPYC_PID=$!

echo "Waiting for RPyC..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',18812)); s.close()" 2>/dev/null; then
        echo "RPyC ready (${i}s)"
        break
    fi
    sleep 1
done

if ! kill -0 $RPYC_PID 2>/dev/null; then
    echo "WARNING: RPyC server may not be ready"
fi

# ── FastAPI bridge ────────────────────────────────────────────────────
echo "Starting bridge on port ${BRIDGE_PORT:-5555}..."
exec /app/venv/bin/python3 -m uvicorn mt5bridge.app:app \
    --host "${BRIDGE_HOST:-0.0.0.0}" \
    --port "${BRIDGE_PORT:-5555}" \
    --log-level info
