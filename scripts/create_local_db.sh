#!/usr/bin/env bash
# Create SeatLock's database user and databases on a local PostgreSQL
# (Postgres.app or Homebrew). Safe to run more than once.
#   bash scripts/create_local_db.sh
set -euo pipefail

PSQL=""
for candidate in /Applications/Postgres.app/Contents/Versions/latest/bin/psql psql; do
  if command -v "$candidate" >/dev/null 2>&1; then PSQL="$candidate"; break; fi
done
if [ -z "$PSQL" ]; then
  echo "ERROR: psql not found. Install Postgres.app (postgresapp.com) and press Initialize."
  exit 1
fi

run() { "$PSQL" -d postgres -v ON_ERROR_STOP=1 -tAc "$1"; }

if [ "$(run "SELECT 1 FROM pg_roles WHERE rolname='booking'")" != "1" ]; then
  run "CREATE ROLE booking LOGIN PASSWORD 'booking' CREATEDB" >/dev/null
  echo "Created database user 'booking'"
fi

for db in booking booking_test; do
  if [ "$(run "SELECT 1 FROM pg_database WHERE datname='$db'")" != "1" ]; then
    run "CREATE DATABASE $db OWNER booking" >/dev/null
    echo "Created database '$db'"
  else
    echo "Database '$db' already exists"
  fi
done

echo "Database ready: postgresql://booking:booking@localhost:5432/booking"
