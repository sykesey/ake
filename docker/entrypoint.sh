#!/bin/bash
set -euo pipefail

echo "Running database migrations..."
alembic upgrade head

echo "Starting AKE..."
exec "$@"
