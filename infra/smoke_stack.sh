#!/usr/bin/env bash
# End-to-end smoke test of the containerised stack (compose project "hossmoke", torn down at exit).
# Verifies: migrations ran, API ready, seeded guest chat answers from the knowledge base and cites
# it, staff login works, the web app renders and proxies to the API, the worker is alive, metrics
# are exposed, and nothing crashed. Set BUILD=0 to reuse already-built images.
set -uo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=hossmoke API_PORT=${API_PORT:-18000} WEB_PORT=${WEB_PORT:-13000}
DC="docker compose -f docker-compose.yml"
fail=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fail=1; }
check() { local name=$1; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi; }
cleanup() { $DC logs --no-color > /tmp/hossmoke-logs.txt 2>&1; $DC down -v >/dev/null 2>&1; }
trap cleanup EXIT

if [ "${BUILD:-1}" = "1" ]; then $DC up -d --build || { echo "FAIL  stack did not start"; exit 1; }
else $DC up -d --no-build || { echo "FAIL  stack did not start"; exit 1; }; fi

API="http://localhost:$API_PORT"; WEB="http://localhost:$WEB_PORT"
for i in $(seq 1 90); do curl -fsS "$API/ready" >/dev/null 2>&1 && break; sleep 1; done
check "api /ready" curl -fsS "$API/ready"
check "migrations applied (alembic head)" bash -c "$DC exec -T postgres psql -U hospitality -d hospitality -tAc \"select version_num from alembic_version\" | grep -q ."
$DC --profile demo run --rm seed >/dev/null 2>&1 && ok "demo seed" || bad "demo seed"

for i in $(seq 1 60); do curl -fsS "$WEB/login" >/dev/null 2>&1 && break; sleep 1; done
check "web /login renders" bash -c "curl -fsS $WEB/login | grep -qi 'sign in'"
check "web home lists seeded hotel (web -> api over the compose network)" bash -c "curl -fsS $WEB/ | grep -q 'Demo Grand Hotel'"
check "web sends CSP and frame protection" bash -c "curl -sI $WEB/login | grep -qi 'content-security-policy' && curl -sI $WEB/login | grep -qi 'x-frame-options\\|frame-ancestors'"

S=$(curl -fsS -X POST "$API/api/v1/chat/sessions" -H 'content-type: application/json' -d '{"org_slug":"demo-group","hotel_slug":"demo-grand"}')
T=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['token'])" "$S" 2>/dev/null)
R=$(curl -fsS -X POST "$API/api/v1/chat/messages" -H "authorization: Bearer $T" -H 'content-type: application/json' -d '{"text":"What time is check-out?","client_message_id":"smoke-1"}')
echo "$R" | grep -q "11:00 AM" && ok "guest chat answers from the knowledge base" || bad "guest chat answer"
echo "$R" | grep -q "Stay Policies" && ok "answer carries a citation" || bad "citation"

L=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/auth/login" -H 'content-type: application/json' -d '{"email":"manager@demo.hotel","password":"demo-password-123"}')
[ "$L" = 200 ] && ok "staff login" || bad "staff login ($L)"
WL=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$WEB/api/auth/login" -H 'content-type: application/json' -H 'x-requested-with: hospitality' -d '{"email":"manager@demo.hotel","password":"demo-password-123"}')
[ "$WL" = 200 ] && ok "staff login through the web proxy" || bad "web login ($WL)"
check "metrics endpoint exposes agent metrics" bash -c "curl -fsS $API/metrics | grep -q agent_turns_total"
sleep 3
check "worker container is running" bash -c "$DC ps --status running --services | grep -qx worker"
check "no tracebacks in api/worker logs" bash -c "! $DC logs api worker 2>&1 | grep -q Traceback"
check "api runs as non-root" bash -c "[ \"\$($DC exec -T api id -u)\" != 0 ]"
check "web runs as non-root" bash -c "[ \"\$($DC exec -T web id -u)\" != 0 ]"
check "postgres/redis are not published to the host" bash -c "! $DC ps --format '{{.Ports}}' postgres redis | grep -q '0.0.0.0'"
# First-run provisioning exactly as documented for production (no demo seed involved).
BOOT_PW="Harbor-Lantern-Quartz-42"
$DC run --rm -e BOOTSTRAP_ADMIN_PASSWORD="$BOOT_PW" api sh -c 'echo "[{\"code\":\"std\",\"name\":\"Standard\",\"max_occupancy\":2,\"base_rate\":\"99.00\",\"count\":2}]" > /tmp/rooms.json && python -m scripts.bootstrap --org-name "Smoke Org" --org-slug smoke-org --hotel-name "Smoke Inn" --hotel-slug smoke-inn --admin-email admin@smoke.example --admin-name "Smoke Admin" --rooms /tmp/rooms.json --emergency-instructions "Call 112." --inventory-days 30' >/dev/null 2>&1 && ok "bootstrap provisions a new organization" || bad "bootstrap"
BL=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/auth/login" -H 'content-type: application/json' -d "{\"email\":\"admin@smoke.example\",\"password\":\"$BOOT_PW\"}")
[ "$BL" = 200 ] && ok "bootstrapped admin can sign in" || bad "bootstrapped admin login ($BL)"
check "bootstrapped hotel is listed publicly" bash -c "curl -fsS $API/api/v1/public/hotels | grep -q smoke-inn"

# Backup and restore: the exact commands in docs/DEPLOYMENT.md, into a scratch database.
PSQL="$DC exec -T postgres psql -U hospitality -tA"
before=$($PSQL -d hospitality -c "select (select count(*) from users)||'/'||(select count(*) from knowledge_documents)||'/'||(select count(*) from messages)")
$DC exec -T postgres pg_dump -U hospitality -Fc hospitality > /tmp/hossmoke.dump
[ -s /tmp/hossmoke.dump ] && ok "pg_dump produced a backup" || bad "pg_dump"
$PSQL -d postgres -c "create database restore_check" >/dev/null
$DC exec -T postgres pg_restore -U hospitality -d restore_check --no-owner < /tmp/hossmoke.dump >/dev/null 2>&1
after=$($PSQL -d restore_check -c "select (select count(*) from users)||'/'||(select count(*) from knowledge_documents)||'/'||(select count(*) from messages)")
[ -n "$before" ] && [ "$before" = "$after" ] && ok "restore reproduces row counts ($before)" || bad "restore mismatch ($before vs $after)"
rm -f /tmp/hossmoke.dump
[ $fail = 0 ] && echo "SMOKE OK" || { echo "SMOKE FAILED (logs: /tmp/hossmoke-logs.txt)"; exit 1; }
