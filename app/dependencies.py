"""FastAPI dependencies shared by the routers."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models import User


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_user_or_404(user_id: int, db: Session = Depends(get_db)) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found. Select or create a profile.")
    return user


class RateLimiter:
    """Sliding-window limiter kept in memory.

    Good enough for one server process. With several processes or servers, use a
    shared store (Redis / ElastiCache) or an AWS API Gateway / WAF rate rule.
    """

    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = max(1, int(60 - (now - hits[0])))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    f"Too many searches in a minute. Try again in {retry} seconds.",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)
