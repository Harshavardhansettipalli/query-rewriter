"""Stage 4 - AI rewriting, followed by Python guardrails.

The AI proposes a rewrite; Python decides whether it is safe to show:
  * not empty, not absurdly long
  * keeps most of the user's key terms (stops "intent drift" and prompt-injection results)
If a check fails, the spelling-corrected query is used instead and the UI says why.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.pipeline.llm_client import LLMClient, LLMError
from app.pipeline.preprocessing import content_words
from app.pipeline.prompts import REWRITING_SYSTEM, rewriting_user_prompt

MIN_TERM_OVERLAP = 0.5  # at least half of the user's key terms must survive


class RewriteResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rewritten_query: str = Field(min_length=1)
    context_added: list[str] = Field(default_factory=list)
    explanation: str = ""

    @field_validator("context_added", mode="before")
    @classmethod
    def _items(cls, v):
        if not isinstance(v, list):
            return []
        return [" ".join(str(x).split())[:80] for x in v if str(x).strip()][:6]

    @field_validator("explanation", mode="before")
    @classmethod
    def _explanation(cls, v):
        return " ".join(str(v or "").split())[:300]


def rewrite(llm: LLMClient, query: str, intent: str, missing: list[str], user_context: dict) -> RewriteResult:
    data = llm.complete_json(REWRITING_SYSTEM, rewriting_user_prompt(query, intent, missing, user_context))
    try:
        return RewriteResult.model_validate(data)
    except ValidationError as exc:
        raise LLMError("AI rewriting returned data in an unexpected shape.") from exc


def clean_rewrite(text: str) -> str:
    text = " ".join(text.split()).strip()
    text = text.strip("\"'“”‘’`")
    return text.rstrip(".").strip()


def _term_kept(term: str, rewritten_words: set[str]) -> bool:
    # Accept simple inflections: "course" kept by "courses", "analysis" by "analyst"-like prefixes.
    stem = term[:5]
    return term in rewritten_words or any(w.startswith(stem) or term.startswith(w[:5]) for w in rewritten_words if len(w) > 3)


def check_rewrite(rewritten: str, corrected: str, *, max_words: int) -> tuple[bool, str, str]:
    """Return (ok, reason_if_not_ok, cleaned_text)."""
    cleaned = clean_rewrite(rewritten)
    if not cleaned:
        return False, "the rewrite was empty", cleaned
    if len(cleaned.split()) > max_words:
        return False, f"the rewrite was longer than {max_words} words", cleaned

    key_terms = set(content_words(corrected))
    if key_terms:
        rewritten_words = set(content_words(cleaned))
        kept = sum(1 for t in key_terms if _term_kept(t, rewritten_words))
        if kept / len(key_terms) < MIN_TERM_OVERLAP:
            return False, "the rewrite dropped your key search terms", cleaned
    return True, "", cleaned
