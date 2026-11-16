"""Stage 2 - AI understanding: intent, context sufficiency, ambiguity, spelling confirmation.

The model's raw JSON is parsed into a strict Pydantic model. Anything missing
gets a safe default; anything malformed becomes an LLMError so the pipeline
falls back instead of showing garbage.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.pipeline.llm_client import LLMClient, LLMError
from app.pipeline.prompts import UNDERSTANDING_SYSTEM, understanding_user_prompt


class SpellingPair(BaseModel):
    model_config = ConfigDict(extra="ignore")
    original: str = Field(min_length=1, max_length=60)
    corrected: str = Field(min_length=1, max_length=60)


class UnderstandingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: str = "general search"
    intent_description: str = ""
    spelling_corrections: list[SpellingPair] = Field(default_factory=list)
    context_sufficient: bool = True
    ambiguity: Literal["none", "low", "high"] = "none"
    ambiguity_reason: str = ""
    missing_context: list[str] = Field(default_factory=list)

    @field_validator("intent", "intent_description", "ambiguity_reason", mode="before")
    @classmethod
    def _short_text(cls, v):
        return " ".join(str(v or "").split())[:200]

    @field_validator("ambiguity", mode="before")
    @classmethod
    def _ambiguity(cls, v):
        v = str(v or "none").strip().lower()
        return v if v in {"none", "low", "high"} else "low"

    @field_validator("missing_context", mode="before")
    @classmethod
    def _missing(cls, v):
        if not isinstance(v, list):
            return []
        return [" ".join(str(x).split())[:50] for x in v if str(x).strip()][:6]

    @field_validator("spelling_corrections", mode="before")
    @classmethod
    def _pairs(cls, v):
        # Drop malformed items instead of failing the whole response.
        if not isinstance(v, list):
            return []
        return [p for p in v if isinstance(p, dict) and p.get("original") and p.get("corrected")][:10]


def understand(llm: LLMClient, query: str, hints: dict[str, list[str]]) -> UnderstandingResult:
    data = llm.complete_json(UNDERSTANDING_SYSTEM, understanding_user_prompt(query, hints))
    try:
        return UnderstandingResult.model_validate(data)
    except ValidationError as exc:
        raise LLMError("AI understanding returned data in an unexpected shape.") from exc
