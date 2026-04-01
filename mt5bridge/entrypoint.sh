#!/bin/bash
set -e

echo "=== MT5 Bridge Starting ==="
echo "Login: ${MT5_LOGIN:-not set}"
echo "Server: ${MT5_SERVER:-not set}"
echo "Port: ${BRIDGE_PORT:-5555}"

# Start virtual display for Wine/MT5
Xvfb :0 -screen 0 1024x768x16 &
sleep 2

# Find MT5 terminal
MT5_EXE=$(find /root/.wine -name "terminal64.exe" 2>/dev/null | head -1)
if [ -z "$MT5_EXE" ]; then
    echo "ERROR: MT5 terminal64.exe not found!"
    echo "Searching for any .exe in wine prefix:"
    find /root/.wine -name "*.exe" 2>/dev/null | head -10
    exit 1
fi

echo "Starting MT5 terminal: $MT5_EXE"
wine64 "$MT5_EXE" /portable &
sleep 10  # Give MT5 time to start and connect

# Export MT5 path for the bridge
export MT5_PATH="$MT5_EXE"

# Start the bridge server
echo "Starting bridge server on port ${BRIDGE_PORT:-5555}..."
exec /app/venv/bin/python3 -m uvicorn mt5bridge.app:app \
    --host "${BRIDGE_HOST:-0.0.0.0}" \
    --port "${BRIDGE_PORT:-5555}" \
    --log-level info
