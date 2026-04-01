#!/bin/bash
set -e

WINE_PY="/root/.wine/drive_c/Python/python.exe"

echo "=== MT5 Bridge Starting ==="
echo "Login: ${MT5_LOGIN:-not set}"
echo "Server: ${MT5_SERVER:-not set}"
echo "Port: ${BRIDGE_PORT:-5555}"

# Virtual display (clean up stale locks from previous runs)
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
Xvfb :0 -screen 0 1024x768x16 &
sleep 2

# MT5 terminal
MT5_EXE=$(find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1)
if [ -n "$MT5_EXE" ]; then
    echo "Starting MT5: $MT5_EXE"
    wine "$MT5_EXE" /portable &
    sleep 10
    export MT5_PATH="$MT5_EXE"
else
    echo "WARNING: MT5 terminal not found"
fi

# RPyC server in Wine Python (bridges Linux → Wine for MetaTrader5 package)
echo "Starting RPyC server..."
wine "$WINE_PY" -c "
from rpyc.utils.server import ThreadedServer
from rpyc import SlaveService
t = ThreadedServer(SlaveService, port=18812, protocol_config={'allow_public_attrs': True, 'allow_all_attrs': True})
t.start()
" &
RPYC_PID=$!

# Wait for RPyC to be ready (retry up to 30 seconds)
echo "Waiting for RPyC server..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',18812)); s.close()" 2>/dev/null; then
        echo "RPyC server ready (took ${i}s)"
        break
    fi
    sleep 1
done

if ! kill -0 $RPYC_PID 2>/dev/null; then
    echo "ERROR: RPyC server died"
    exit 1
fi

# FastAPI bridge
echo "Starting bridge on port ${BRIDGE_PORT:-5555}..."
exec /app/venv/bin/python3 -m uvicorn mt5bridge.app:app \
    --host "${BRIDGE_HOST:-0.0.0.0}" \
    --port "${BRIDGE_PORT:-5555}" \
    --log-level info
