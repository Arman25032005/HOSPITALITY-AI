"""Worker entrypoint: python -m worker.main [--once]. Needs the same environment as the API."""
import argparse
import logging
import signal
import time

from app.config import get_settings
from app.db.session import SessionLocal
from worker.tasks import TASKS

log = logging.getLogger("hospitality.worker")


def run_once(only: set[str] | None = None) -> dict[str, int]:
    settings = get_settings()
    results = {}
    for name, (fn, _) in TASKS.items():
        if only and name not in only:
            continue
        try:
            with SessionLocal() as db:
                results[name] = fn(db, settings)
        except Exception:
            log.exception("task %s failed", name)
            results[name] = -1
    return results


def run_forever(poll: float = 2.0) -> None:
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    last: dict[str, float] = {}
    log.info("worker started")
    while not stop["now"]:
        now = time.monotonic()
        due = {n for n, (_, every) in TASKS.items() if now - last.get(n, -1e9) >= every}
        if due:
            for n, count in run_once(due).items():
                last[n] = now
                if count:
                    log.info("task %s processed %s", n, count)
        time.sleep(poll)
    log.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run every task once and exit")
    args = ap.parse_args()
    if args.once:
        print(run_once())
    else:
        run_forever()
