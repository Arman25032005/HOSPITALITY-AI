# Worker

DB-backed background worker (no broker needed): retries failed channel deliveries with backoff,
purges conversations past retention, and reaps ingestions/webhooks stuck mid-flight.

```bash
cd apps/api && . .venv/bin/activate
PYTHONPATH=../worker python -m worker.main --once   # run every task once
PYTHONPATH=../worker python -m worker.main          # run continuously (SIGTERM stops it)
```

Celery was not used: the queues here are rows with due dates, claimed with `SKIP LOCKED`, which
keeps local setup simple. Moving to Celery/SQS is possible later if throughput requires it.
