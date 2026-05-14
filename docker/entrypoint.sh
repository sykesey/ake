#!/bin/bash
set -euo pipefail

echo "Running database migrations..."
python -m alembic upgrade head

echo "Starting AKE..."
exec "$@"
