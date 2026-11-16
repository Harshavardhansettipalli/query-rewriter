"""Run the rewriter and record the user's decision."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database import utcnow
from app.dependencies import get_db
from app.models import Activity, QueryLog, User
from app.pipeline.orchestrator import QueryRewriterPipeline
from app.pipeline.preprocessing import normalize_query, validate_query
from app.schemas import DecisionRequest, DecisionResponse, RewriteRequest, RewriteResponse

router = APIRouter(prefix="/api", tags=["rewriter"])


@router.post("/rewrite", response_model=RewriteResponse)
def rewrite_query(body: RewriteRequest, request: Request, db: Session = Depends(get_db)):
    """Run the five-stage pipeline. Nothing is searched until the user decides."""
    state = request.app.state
    state.rate_limiter.check(f"user:{body.user_id}")

    # NOTE: in a real portal the user id comes from the login session/JWT,
    # never from the request body. See README > Security.
    user = db.get(User, body.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found. Select or create a profile.")

    pipeline = QueryRewriterPipeline(db, state.settings, state.llm, state.spelling)
    log = pipeline.run(user, body.query)
    return RewriteResponse(query_id=log.id, **log.result)


@router.post("/queries/{query_id}/decision", response_model=DecisionResponse)
def decide(query_id: int, body: DecisionRequest, request: Request, db: Session = Depends(get_db)):
    """Accept the suggestion, save an edited version, or reject it and keep the original."""
    log = db.get(QueryLog, query_id)
    if log is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This suggestion no longer exists.")

    settings = request.app.state.settings
    if body.action == "accept":
        final, new_status = log.suggested_query, "accepted"
    elif body.action == "reject":
        final, new_status = log.original_query, "rejected"
    else:
        final, _ = normalize_query(body.edited_query or "")
        validate_query(final, min_length=settings.min_query_length, max_length=settings.max_query_length)
        new_status = "edited"

    # Conditional update = protection against double clicks / two open tabs deciding at once.
    result = db.execute(
        update(QueryLog)
        .where(QueryLog.id == log.id, QueryLog.status == "pending")
        .values(status=new_status, final_query=final, decided_at=utcnow())
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A decision was already recorded for this suggestion.")

    # The final search becomes portal activity, which improves future context.
    db.add(Activity(user_id=log.user_id, event_type="search", content=final[:300]))
    db.commit()
    return DecisionResponse(query_id=log.id, status=new_status, final_query=final)
