#!/bin/bash
set -e

export WINEDEBUG=-all
export DISPLAY=:0
export WINEDLLOVERRIDES="mscoree,mshtml=;dbghelp=d"

WINE_PY="/root/.wine/drive_c/Python/python.exe"

echo "=== MT5 Bridge Starting ==="
echo "Login: ${MT5_LOGIN:-not set}"
echo "Server: ${MT5_SERVER:-not set}"

# ── Display + Window Manager ──────────────────────────────────────────
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
Xvfb :0 -screen 0 1280x720x24 &
sleep 1
openbox &
sleep 1

# ── VNC ───────────────────────────────────────────────────────────────
x11vnc -display :0 -forever -nopw -rfbport 5900 -bg -q 2>/dev/null || true
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
sleep 1
echo "VNC: http://$(hostname -I | awk '{print $1}'):6080/vnc.html"

# ── MT5 terminal ─────────────────────────────────────────────────────
MT5_EXE=$(find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1)
if [ -n "$MT5_EXE" ]; then
    echo "Starting MT5: $MT5_EXE"
    wine "$MT5_EXE" /portable &
    sleep 5
else
    echo ""
    echo "============================================="
    echo "  MT5 NOT INSTALLED"
    echo "  Open VNC and run the installer manually:"
    echo "  Right-click desktop → Terminal → type:"
    echo "  wine C:\\\\mt5setup.exe"
    echo "============================================="
    echo ""
fi

# ── RPyC server ───────────────────────────────────────────────────────
echo "Starting RPyC server..."
wine "$WINE_PY" -c "
from rpyc.utils.server import ThreadedServer
from rpyc import SlaveService
t = ThreadedServer(SlaveService, port=18812, protocol_config={'allow_public_attrs': True, 'allow_all_attrs': True})
t.start()
" &

echo "Waiting for RPyC..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',18812)); s.close()" 2>/dev/null; then
        echo "RPyC ready (${i}s)"
        break
    fi
    sleep 1
done

# ── FastAPI bridge ────────────────────────────────────────────────────
echo "Starting bridge on port ${BRIDGE_PORT:-5555}..."
exec /app/venv/bin/python3 -m uvicorn mt5bridge.app:app \
    --host "${BRIDGE_HOST:-0.0.0.0}" \
    --port "${BRIDGE_PORT:-5555}" \
    --log-level info
