"""The hybrid pipeline: Python and AI each do what they're good at.

    User query
      -> 1. Python preprocessing   (validate, normalise, detect spelling, duplicate check)
      -> 2. AI understanding       (intent, context sufficiency, ambiguity, confirm spelling)
      -> 3. Python context         (profile + recent activity + preferences; only if needed)
      -> 4. AI rewriting           (add relevant context; Python guardrails check the output)
      -> 5. User approval          (accept / edit / reject)

Cost and latency rules:
  * If the query is already specific, stages 3 and 4 are skipped (one AI call, not two).
  * An identical query from the same user within the duplicate window reuses the
    earlier result (zero AI calls).
  * If the AI is unavailable, the user still gets rule-based spelling fixes and a
    clear notice - the search box never breaks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from time import perf_counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.models import Activity, QueryLog, User
from app.pipeline.context import gather_user_context, recent_activities
from app.pipeline.llm_client import LLMClient, LLMError
from app.pipeline.preprocessing import (
    SpellingDetector,
    build_vocabulary,
    edit_distance,
    preprocess,
)
from app.pipeline.rewriting import check_rewrite, rewrite
from app.pipeline.understanding import UnderstandingResult, understand

logger = logging.getLogger(__name__)


@dataclass
class StepRecorder:
    steps: list[dict] = field(default_factory=list)

    def add(self, key: str, title: str, engine: str, status: str, summary: str, started: float | None = None):
        duration = int((perf_counter() - started) * 1000) if started is not None else 0
        self.steps.append({"key": key, "title": title, "engine": engine, "status": status,
                           "summary": summary, "duration_ms": duration})


def _join(parts: list[str]) -> str:
    return " ".join(p for p in parts if p)


class QueryRewriterPipeline:
    def __init__(self, db: Session, settings: Settings, llm: LLMClient | None, spelling: SpellingDetector):
        self.db = db
        self.settings = settings
        self.llm = llm
        self.spelling = spelling

    # ------------------------------------------------------------------ public
    def run(self, user: User, raw_query: str) -> QueryLog:
        rec = StepRecorder()
        s = self.settings

        # ---------- Stage 1: Python preprocessing --------------------------------
        started = perf_counter()
        activities = recent_activities(self.db, user.id, s.recent_activity_limit)
        vocabulary = build_vocabulary(user.interests or [], user.role, user.preferences or {},
                                      [a.content for a in activities])
        pre = preprocess(raw_query, min_length=s.min_query_length, max_length=s.max_query_length,
                         detector=self.spelling, vocabulary=vocabulary)  # raises QueryValidationError

        duplicate = self._find_reusable(user, pre.dedup_key)
        if duplicate is not None:
            return self._reuse(user, raw_query, pre.dedup_key, duplicate, started)

        hints = pre.misspellings
        spelling_note = (
            "Possible misspellings: " + ", ".join(hints) + "."
            if hints else "No likely misspellings found."
        )
        rec.add("preprocess", "Python preprocessing", "python", "done",
                _join([f"Query is valid ({pre.word_count} word{'s' if pre.word_count != 1 else ''}).",
                       *pre.notes, spelling_note, "No recent duplicate."]), started)

        # ---------- Stage 2: AI understanding -------------------------------------
        started = perf_counter()
        understanding: UnderstandingResult | None = None
        notice: str | None = None
        if self.llm is None:
            notice = "AI is not configured, so only dictionary spelling fixes were applied. Add OPENAI_API_KEY to enable rewriting."
            rec.add("understand", "AI understanding", "llm", "skipped",
                    "Skipped: no OpenAI API key configured. Used the dictionary's spelling suggestions instead.", started)
        else:
            try:
                understanding = understand(self.llm, pre.normalized, hints)
                u = understanding
                missing = f" Missing: {', '.join(u.missing_context)}." if u.missing_context else ""
                rec.add("understand", "AI understanding", "llm", "done",
                        f"Intent: {u.intent}. Context is {'sufficient' if u.context_sufficient else 'not sufficient'}."
                        f"{missing} Ambiguity: {u.ambiguity}.", started)
            except LLMError as exc:
                logger.warning("Understanding failed for user %s: %s", user.id, exc)
                notice = f"{exc} Only dictionary spelling fixes were applied."
                rec.add("understand", "AI understanding", "llm", "fallback",
                        f"AI step failed ({exc}). Used the dictionary's spelling suggestions instead.", started)

        # Spelling is applied by Python, so only spelling can change at this point.
        fixes = self._spelling_fixes(pre.normalized, understanding, hints)
        corrected = SpellingDetector.apply(pre.normalized, {f["original"]: f["corrected"] for f in fixes})

        needs_context = understanding is not None and (
            not understanding.context_sufficient or understanding.ambiguity == "high"
        )

        # ---------- Stage 3: Python context retrieval -----------------------------
        started = perf_counter()
        user_ctx = None
        if needs_context:
            user_ctx = gather_user_context(self.db, user, corrected, fetch_limit=s.recent_activity_limit,
                                           keep_limit=s.context_activity_limit)
            rec.add("context", "Profile and activity lookup", "python", "done",
                    ("Loaded " + "; ".join(user_ctx.summary) + ".") if not user_ctx.is_empty
                    else "This profile has no details or activity yet, so only clarity can be improved.",
                    started)
        else:
            rec.add("context", "Profile and activity lookup", "python", "skipped",
                    "Not needed: the query is specific enough, so your profile wasn't used."
                    if understanding else "Skipped because AI understanding was unavailable.")

        # ---------- Stage 4: AI rewriting + Python guardrails ---------------------
        started = perf_counter()
        suggested, context_used, explanation = corrected, [], ""
        if needs_context:
            try:
                result = rewrite(self.llm, corrected, understanding.intent, understanding.missing_context,
                                 user_ctx.data if user_ctx else {})
                ok, reason, cleaned = check_rewrite(result.rewritten_query, corrected, max_words=s.max_rewrite_words)
                if ok:
                    suggested = cleaned
                    context_used = result.context_added
                    explanation = result.explanation
                    rec.add("rewrite", "AI rewriting", "llm", "done",
                            explanation or "Rewrote the query with relevant context. Safety checks passed.", started)
                else:
                    logger.info("Rewrite rejected for user %s: %s", user.id, reason)
                    rec.add("rewrite", "AI rewriting", "llm", "fallback",
                            f"The AI's suggestion was rejected by a safety check ({reason}). Kept your query with spelling fixes.",
                            started)
            except LLMError as exc:
                logger.warning("Rewrite failed for user %s: %s", user.id, exc)
                notice = f"{exc} Showing your query with spelling fixes only."
                rec.add("rewrite", "AI rewriting", "llm", "fallback",
                        f"AI step failed ({exc}). Kept your query with spelling fixes.", started)
        else:
            rec.add("rewrite", "AI rewriting", "llm", "skipped",
                    "Not needed: no context had to be added." if understanding else
                    "Skipped because AI understanding was unavailable.")

        changed = " ".join(suggested.split()).casefold() != " ".join(raw_query.split()).casefold()
        if not explanation:
            if fixes:
                explanation = "Fixed spelling: " + ", ".join(f"{f['original']} → {f['corrected']}" for f in fixes) + "."
            elif changed:
                explanation = "Cleaned up spacing and repeated words."
            else:
                explanation = "Your query is already clear. No changes needed."

        # ---------- Stage 5: waiting for the user ---------------------------------
        rec.add("approval", "Your decision", "user", "pending", "Accept, edit, or reject the suggestion.")

        payload = {
            "original_query": raw_query.strip(),
            "corrected_query": corrected,
            "suggested_query": suggested,
            "changed": changed,
            "intent": understanding.intent if understanding else None,
            "intent_description": understanding.intent_description if understanding else None,
            "context_sufficient": understanding.context_sufficient if understanding else None,
            "ambiguity": understanding.ambiguity if understanding else None,
            "ambiguity_reason": (understanding.ambiguity_reason or None) if understanding else None,
            "spelling_fixes": fixes,
            "context_used": context_used,
            "explanation": explanation,
            "steps": rec.steps,
            "llm_used": understanding is not None,
            "degraded": notice is not None,
            "notice": notice,
            "reused_from": None,
        }
        return self._save(user, pre.dedup_key, payload, reused_from=None)

    # ----------------------------------------------------------------- helpers
    def _spelling_fixes(self, text: str, understanding: UnderstandingResult | None,
                        hints: dict[str, list[str]]) -> list[dict]:
        words_in_query = {w.lower() for w in text.split()}
        words_in_query |= {w.strip(".,!?;:").lower() for w in text.split()}
        fixes: list[dict] = []
        if understanding is not None:
            # The AI has the final say, but only for words that are really in the query.
            for pair in understanding.spelling_corrections:
                original, corrected = pair.original.strip(), pair.corrected.strip()
                if original.lower() in words_in_query and original.lower() != corrected.lower():
                    fixes.append({"original": original.lower(), "corrected": corrected.lower()})
        else:
            # Without the AI there is no context check, so only apply near-certain fixes
            # (a single typo: one missing, extra, wrong or swapped letter).
            for word, candidates in hints.items():
                if candidates and edit_distance(word, candidates[0]) == 1:
                    fixes.append({"original": word, "corrected": candidates[0]})
        return fixes

    def _find_reusable(self, user: User, dedup_key: str) -> QueryLog | None:
        window_start = utcnow() - timedelta(seconds=self.settings.duplicate_window_seconds)
        candidate = self.db.scalars(
            select(QueryLog)
            .where(QueryLog.user_id == user.id, QueryLog.dedup_key == dedup_key,
                   QueryLog.created_at >= window_start, QueryLog.status != "rejected",
                   QueryLog.degraded.is_(False), QueryLog.reused_from_id.is_(None))
            .order_by(QueryLog.created_at.desc())
            .limit(1)
        ).first()
        if candidate is None:
            return None
        # If the profile or (non-search) activity changed since, the old answer may be stale.
        if user.updated_at and user.updated_at > candidate.created_at:
            return None
        newest_activity = self.db.scalar(
            select(func.max(Activity.created_at))
            .where(Activity.user_id == user.id, Activity.event_type != "search")
        )
        if newest_activity and newest_activity > candidate.created_at:
            return None
        return candidate

    def _reuse(self, user: User, raw_query: str, dedup_key: str, source: QueryLog, started: float) -> QueryLog:
        age = int((utcnow() - source.created_at).total_seconds())
        payload = dict(source.result)
        payload["original_query"] = raw_query.strip()
        payload["reused_from"] = source.id
        payload["notice"] = None
        payload["changed"] = (
            " ".join(payload["suggested_query"].split()).casefold() != " ".join(raw_query.split()).casefold()
        )
        rec = StepRecorder()
        rec.add("preprocess", "Python preprocessing", "python", "done",
                f"Query is valid. You searched for this {age} seconds ago, so that result was reused "
                "instead of calling the AI again.", started)
        for step in source.result.get("steps", [])[1:-1]:
            rec.steps.append({**step, "summary": "Reused: " + step["summary"], "duration_ms": 0})
        rec.add("approval", "Your decision", "user", "pending", "Accept, edit, or reject the suggestion.")
        payload["steps"] = rec.steps
        return self._save(user, dedup_key, payload, reused_from=source.id)

    def _save(self, user: User, dedup_key: str, payload: dict, reused_from: int | None) -> QueryLog:
        log = QueryLog(
            user_id=user.id,
            original_query=payload["original_query"],
            dedup_key=dedup_key,
            suggested_query=payload["suggested_query"],
            status="pending",
            intent=(payload.get("intent") or "")[:100] or None,
            llm_used=bool(payload["llm_used"]) and reused_from is None,
            degraded=bool(payload["degraded"]),
            reused_from_id=reused_from,
            result=payload,
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        logger.info("Query %s for user %s: changed=%s degraded=%s reused=%s",
                    log.id, user.id, payload["changed"], payload["degraded"], reused_from)
        return log
