"""Request and response models. Every input from the browser is validated here
before it reaches the database or the AI."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Stored as naive UTC; always serialised with a 'Z' so browsers convert to local time correctly.
UTCDateTime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]

EVENT_TYPES = ("search", "view", "click", "download", "bookmark", "purchase", "enroll")
EventType = Literal["search", "view", "click", "download", "bookmark", "purchase", "enroll"]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


# --------------------------------------------------------------------------- users
class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=100)
    interests: list[str] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Name is required.")
        return v

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v):
        v = _clean_optional(v)
        if v is None:
            return None
        v = v.lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Email address is not valid.")
        return v

    @field_validator("role", "location", mode="before")
    @classmethod
    def _optional_text(cls, v):
        return _clean_optional(v)

    @field_validator("interests")
    @classmethod
    def _interests(cls, values: list[str]) -> list[str]:
        cleaned, seen = [], set()
        for item in values:
            item = " ".join(str(item).split())[:50]
            if item and item.lower() not in seen:
                seen.add(item.lower())
                cleaned.append(item)
        if len(cleaned) > 20:
            raise ValueError("Use at most 20 interests.")
        return cleaned

    @field_validator("preferences")
    @classmethod
    def _preferences(cls, values: dict[str, str]) -> dict[str, str]:
        cleaned = {}
        for key, val in values.items():
            key, val = " ".join(str(key).split())[:40], " ".join(str(val).split())[:100]
            if key and val:
                cleaned[key] = val
        if len(cleaned) > 20:
            raise ValueError("Use at most 20 preferences.")
        return cleaned


class UserOut(UserIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


# ---------------------------------------------------------------------- activity
class ActivityIn(BaseModel):
    event_type: EventType
    content: str = Field(min_length=1, max_length=300)

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Describe the activity.")
        return v


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    content: str
    created_at: UTCDateTime


# ----------------------------------------------------------------------- rewrite
class RewriteRequest(BaseModel):
    user_id: int = Field(gt=0)
    # Hard upper bound to stop abuse; the friendlier length rule lives in preprocessing.
    query: str = Field(max_length=2000)


class SpellingFix(BaseModel):
    original: str
    corrected: str


class PipelineStep(BaseModel):
    key: str
    title: str
    engine: Literal["python", "llm", "user"]
    status: Literal["done", "skipped", "fallback", "pending"]
    summary: str
    duration_ms: int = 0


class RewriteResponse(BaseModel):
    query_id: int
    original_query: str
    corrected_query: str
    suggested_query: str
    changed: bool
    intent: str | None
    intent_description: str | None
    context_sufficient: bool | None
    ambiguity: Literal["none", "low", "high"] | None
    ambiguity_reason: str | None
    spelling_fixes: list[SpellingFix]
    context_used: list[str]
    explanation: str
    steps: list[PipelineStep]
    llm_used: bool
    degraded: bool
    notice: str | None
    reused_from: int | None


class DecisionRequest(BaseModel):
    action: Literal["accept", "edit", "reject"]
    edited_query: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _edit_needs_text(self):
        if self.action == "edit" and not (self.edited_query and self.edited_query.strip()):
            raise ValueError("Enter the edited query.")
        return self


class DecisionResponse(BaseModel):
    query_id: int
    status: str
    final_query: str


class QueryHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    original_query: str
    suggested_query: str
    final_query: str | None
    status: str
    intent: str | None
    degraded: bool
    created_at: UTCDateTime
    decided_at: UTCDateTime | None


class HealthOut(BaseModel):
    status: str
    ai_enabled: bool
    model: str | None
    database: str
