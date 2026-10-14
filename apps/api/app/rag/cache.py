import threading
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import metrics
from app.models import KnowledgeDocument


class AnswerCache:
    """Small in-process TTL cache for guest knowledge answers.

    The key includes a knowledge-base version (document count and latest change), so uploading,
    replacing, deactivating or deleting a document invalidates cached answers immediately."""

    def __init__(self, clock=time.monotonic, max_items: int = 500):
        self.clock, self.max_items = clock, max_items
        self._items: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def kb_version(db: Session, hotel_id) -> tuple:
        n, latest = db.execute(select(func.count(KnowledgeDocument.id),
                                      func.max(KnowledgeDocument.updated_at)).where(
            KnowledgeDocument.hotel_id == hotel_id, KnowledgeDocument.is_active.is_(True),
            KnowledgeDocument.status == "indexed")).one()
        return (n, latest.isoformat() if latest else None)

    def get(self, key: tuple):
        with self._lock:
            hit = self._items.get(key)
            if hit and hit[0] > self.clock():
                metrics.KB_CACHE.labels("hit").inc()
                return hit[1]
            self._items.pop(key, None)
        metrics.KB_CACHE.labels("miss").inc()
        return None

    def put(self, key: tuple, value: object, ttl: float) -> None:
        with self._lock:
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (self.clock() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


answer_cache = AnswerCache()
