#!/bin/bash
set -e

WINE_PYTHON="wine64 /root/.wine/drive_c/Python312/python.exe"

echo "=== MT5 Bridge Starting ==="
echo "Login: ${MT5_LOGIN:-not set}"
echo "Server: ${MT5_SERVER:-not set}"
echo "Port: ${BRIDGE_PORT:-5555}"

# Start virtual display
Xvfb :0 -screen 0 1024x768x16 &
sleep 2

# Start MT5 terminal
MT5_EXE=$(find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1)
if [ -n "$MT5_EXE" ]; then
    echo "Starting MT5: $MT5_EXE"
    wine64 "$MT5_EXE" /portable &
    sleep 10
    export MT5_PATH="$MT5_EXE"
else
    echo "WARNING: MT5 terminal not found"
fi

# Start RPyC server in Wine Python
echo "Starting RPyC server (Wine Python)..."
$WINE_PYTHON -c "
from rpyc.utils.server import ThreadedServer
from rpyc import SlaveService
t = ThreadedServer(SlaveService, port=18812, protocol_config={'allow_public_attrs': True, 'allow_all_attrs': True})
t.start()
" &
sleep 5
echo "RPyC server ready"

# Start FastAPI bridge (Linux Python)
echo "Starting bridge on port ${BRIDGE_PORT:-5555}..."
exec /app/venv/bin/python3 -m uvicorn mt5bridge.app:app \
    --host "${BRIDGE_HOST:-0.0.0.0}" \
    --port "${BRIDGE_PORT:-5555}" \
    --log-level info
