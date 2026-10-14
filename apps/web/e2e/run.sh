#!/usr/bin/env bash
# Builds and runs the whole stack against an isolated database, then runs the browser tests.
# Needs: PostgreSQL reachable as hospitality/hospitality on localhost:5432, apps/api/.venv, Chromium.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DB="${E2E_DB:-hospitality_e2e}"
API_PORT=8801; WEB_PORT=3301
export DATABASE_URL="postgresql+psycopg://hospitality:hospitality@localhost:5432/$DB"
export ENVIRONMENT=development APP_SECRET_KEY=e2e-secret-key-not-for-production-use-0123456789
export CHAT_SESSION_RATE_PER_MINUTE=1000 CHAT_MESSAGE_RATE_PER_MINUTE=1000 LOGIN_RATE_PER_WINDOW=1000
export CORS_ORIGINS="" API_INTERNAL_URL="http://localhost:$API_PORT"

PGPASSWORD=hospitality psql -h localhost -U hospitality -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
cd "$ROOT/apps/api" && . .venv/bin/activate
alembic upgrade head >/dev/null && python -m scripts.seed >/dev/null
uvicorn app.main:app --port $API_PORT --log-level warning > /tmp/e2e-api.log 2>&1 &
API_PID=$!
cd "$ROOT/apps/web"
[ -d .next ] || npm run build >/dev/null
setsid npx next start -p $WEB_PORT > /tmp/e2e-web.log 2>&1 &
WEB_PID=$!
trap 'kill $API_PID 2>/dev/null || true; kill -- -$WEB_PID 2>/dev/null || true' EXIT
for i in $(seq 1 60); do
  curl -sf localhost:$API_PORT/ready >/dev/null && curl -sf localhost:$WEB_PORT/login >/dev/null && break; sleep 1
done
E2E_API="http://localhost:$API_PORT" E2E_WEB="http://localhost:$WEB_PORT" npx playwright test "$@"
