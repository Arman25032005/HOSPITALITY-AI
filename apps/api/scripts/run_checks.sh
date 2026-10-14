#!/usr/bin/env bash
# Run every automated check and write machine-readable reports to ./reports (repo root).
#   TEST_DATABASE_URL  database for pytest   (name must end in _test)
#   EVAL_DATABASE_URL  database for evals    (name must end in _eval; schema migrated)
# Both are TRUNCATED/dropped by the checks. Web e2e is run separately (apps/web/e2e/run.sh).
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="$(cd ../.. && pwd)/reports"
mkdir -p "$OUT"
rc=0
: "${TEST_DATABASE_URL:?set TEST_DATABASE_URL (…_test)}"
: "${EVAL_DATABASE_URL:?set EVAL_DATABASE_URL (…_eval)}"

ruff check . || rc=1
DATABASE_URL="$TEST_DATABASE_URL" ENVIRONMENT=test \
  pytest -q -p no:cacheprovider --junitxml="$OUT/junit.xml" || rc=1

export DATABASE_URL="$EVAL_DATABASE_URL" ENVIRONMENT=test
python -m scripts.eval_agent --out "$OUT/agent.json" || rc=1
python - <<'PY'
from sqlalchemy import text
from app.db.session import SessionLocal
with SessionLocal() as db:  # eval_agent leaves its last scenario's data behind: start RAG clean
    db.execute(text("TRUNCATE organizations CASCADE")); db.commit()
PY
python -m scripts.seed >/dev/null
python -m scripts.eval_rag --out "$OUT/rag.json" >/dev/null || rc=1
[ -f ../web/reports/e2e-results.json ] && cp ../web/reports/e2e-results.json "$OUT/e2e.json"

python -m scripts.aggregate_reports --junit "$OUT/junit.xml" --agent "$OUT/agent.json" \
  --rag "$OUT/rag.json" --e2e "$OUT/e2e.json" --out "$OUT/summary.json" || rc=1
exit $rc
