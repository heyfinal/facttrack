#!/bin/bash
# Initialize the FactTrack PostgreSQL database + apply the canonical schema.
# Idempotent — safe to run multiple times.
set -euo pipefail

DB_NAME="${FT_DB_NAME:-facttrack}"
DB_USER="${FT_DB_USER:-daniel}"
DB_HOST="${FT_DB_HOST:-127.0.0.1}"
DB_PORT="${FT_DB_PORT:-5432}"

SCHEMA_SQL="$(cd "$(dirname "$0")/.." && pwd)/sql/schema.sql"

if [ ! -f "$SCHEMA_SQL" ]; then
  echo "ERROR: schema file not found at $SCHEMA_SQL" >&2
  exit 1
fi

# 1. Ensure the role exists (idempotent)
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE \"$DB_USER\" WITH LOGIN SUPERUSER"

# 2. Ensure the database exists (idempotent)
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 || \
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

# 3. Apply the schema (idempotent — schema.sql uses CREATE IF NOT EXISTS)
PGPASSWORD="${FT_DB_PASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SCHEMA_SQL"

echo "DB ready: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
