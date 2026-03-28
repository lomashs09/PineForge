#!/bin/bash
# PineForge Database Setup Script
# Run this once to create the database and apply migrations.

set -e

DB_NAME="pineforge"
DB_USER=$(whoami)

echo "=== PineForge Database Setup ==="

# Check PostgreSQL is running
if ! pg_isready -q; then
    echo "ERROR: PostgreSQL is not running. Start it first."
    exit 1
fi
echo "PostgreSQL is running."

# Create database if it doesn't exist
if psql -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    echo "Database '$DB_NAME' already exists."
else
    echo "Creating database '$DB_NAME'..."
    createdb "$DB_NAME"
    echo "Database created."
fi

# Run Alembic migrations
echo "Running migrations..."
cd "$(dirname "$0")/.."
python3 -m alembic upgrade head

echo ""
echo "=== Setup Complete ==="
echo "Database: $DB_NAME"
echo "User:     $DB_USER"
echo "URL:      postgresql+asyncpg://$DB_USER@localhost:5432/$DB_NAME"
echo ""
echo "Start the server with:"
echo "  python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000"
echo "  Swagger UI: http://localhost:8000/docs"
