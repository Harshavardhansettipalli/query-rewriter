"""Stage 3 - Python context retrieval.

Only runs when the AI said the query lacks context. Pulls the user's profile,
recent portal activity and preferences from the database, ranks activity by
relevance to the query, and sends only the top few items to the AI.

Privacy: name and email are never sent to OpenAI. Only role, location,
interests, preferences and short activity descriptions are shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import Activity, User
from app.pipeline.preprocessing import content_words

_PAST_TENSE = {
    "search": "searched for", "view": "viewed", "click": "opened", "download": "downloaded",
    "bookmark": "bookmarked", "purchase": "purchased", "enroll": "enrolled in",
}


@dataclass
class UserContext:
    data: dict = field(default_factory=dict)       # what is sent to the AI
    summary: list[str] = field(default_factory=list)  # what the UI shows
    activity_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.data


def relative_time(when: datetime, now: datetime) -> str:
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 3600:
        return "within the last hour"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def recent_activities(db: Session, user_id: int, limit: int) -> list[Activity]:
    stmt = (
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(Activity.created_at.desc(), Activity.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def rank_activities(activities: list[Activity], query: str, keep: int) -> list[Activity]:
    """Score = word overlap with the query (weighted) + recency. Newest wins ties."""
    query_words = set(content_words(query))
    total = len(activities)
    scored = []
    for position, act in enumerate(activities):  # activities arrive newest first
        overlap = len(query_words & set(content_words(act.content)))
        recency = (total - position) / max(total, 1)
        scored.append((overlap * 2 + recency, act))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [act for _, act in scored[:keep]]


def gather_user_context(db: Session, user: User, query: str, *, fetch_limit: int,
                        keep_limit: int) -> UserContext:
    ctx = UserContext()
    now = utcnow()

    if user.role:
        ctx.data["role"] = user.role
        ctx.summary.append(f"Role: {user.role}")
    if user.location:
        ctx.data["location"] = user.location
        ctx.summary.append(f"Location: {user.location}")
    if user.interests:
        ctx.data["interests"] = list(user.interests)
        ctx.summary.append("Interests: " + ", ".join(user.interests))
    if user.preferences:
        ctx.data["preferences"] = dict(user.preferences)
        ctx.summary.append("Preferences: " + ", ".join(f"{k} = {v}" for k, v in user.preferences.items()))

    ranked = rank_activities(recent_activities(db, user.id, fetch_limit), query, keep_limit)
    if ranked:
        ctx.data["recent_activity"] = [
            {"action": _PAST_TENSE.get(a.event_type, a.event_type), "item": a.content,
             "when": relative_time(a.created_at, now)}
            for a in ranked
        ]
        ctx.activity_count = len(ranked)
        ctx.summary.append(
            f"{len(ranked)} recent portal "
            f"{'activity' if len(ranked) == 1 else 'activities'}, most relevant first"
        )
    return ctx
