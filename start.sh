#!/bin/bash
set -e

echo "Starting MarineGuard..."

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found! Please ensure it's created."
    exit 1
fi

# Go to backend directory
cd backend

# Apply any pending database migrations before starting the server.
# This used to be a manual step (`alembic upgrade head`) — folding it in
# here so a plain ./start.sh always runs against an up-to-date schema.
echo "Applying database migrations..."
alembic upgrade head

# Start the FastAPI server
echo "Starting FastAPI server on http://localhost:8000..."
exec uvicorn app.main:app --reload --port 8000
