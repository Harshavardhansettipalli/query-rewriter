"""Database tables: user profiles, portal activity, and every rewrite request."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    interests: Mapped[list] = mapped_column(JSON, default=list)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    activities: Mapped[list["Activity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    queries: Mapped[list["QueryLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Activity(Base):
    """Something the user did on the portal (searched, viewed, downloaded...)."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User] = relationship(back_populates="activities")


class QueryLog(Base):
    """One run of the pipeline plus the user's decision. This is the audit trail."""

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_query: Mapped[str] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(400), index=True)
    suggested_query: Mapped[str] = mapped_column(Text)
    final_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | accepted | edited | rejected
    status: Mapped[str] = mapped_column(String(20), default="pending")
    intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    reused_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Full response payload, including the step-by-step trace shown in the UI.
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="queries")
