#!/bin/bash
set -e

echo "=== MT5 Bridge Starting ==="
echo "Login: ${MT5_LOGIN:-not set}"
echo "Server: ${MT5_SERVER:-not set}"
echo "Port: ${BRIDGE_PORT:-5555}"

# Start virtual display for Wine/MT5
echo "Starting Xvfb..."
Xvfb :0 -screen 0 1024x768x16 &
sleep 2

# Find and start MT5 terminal
MT5_EXE=$(find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1)
if [ -n "$MT5_EXE" ]; then
    echo "Starting MT5 terminal: $MT5_EXE"
    wine64 "$MT5_EXE" /portable &
    sleep 10
    export MT5_PATH="$MT5_EXE"
else
    echo "WARNING: MT5 terminal not found, will try to initialize without it"
fi

# Start RPyC server in Wine Python (bridges Linux → Wine for MetaTrader5 package)
echo "Starting RPyC server in Wine Python..."
wine64 python -c "
from rpyc.utils.server import ThreadedServer
from rpyc import SlaveService
print('RPyC server starting on port 18812...')
t = ThreadedServer(SlaveService, port=18812, protocol_config={'allow_public_attrs': True, 'allow_all_attrs': True})
t.start()
" &
sleep 5
echo "RPyC server started"

# Start the bridge FastAPI server (Linux Python)
echo "Starting bridge server on port ${BRIDGE_PORT:-5555}..."
exec /app/venv/bin/python3 -m uvicorn mt5bridge.app:app \
    --host "${BRIDGE_HOST:-0.0.0.0}" \
    --port "${BRIDGE_PORT:-5555}" \
    --log-level info
