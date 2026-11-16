"""Profiles, portal activity and search history.

FastAPI caches dependencies per request, so `get_user_or_404` and the route share
the same database session.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_user_or_404
from app.models import Activity, QueryLog, User
from app.schemas import ActivityIn, ActivityOut, QueryHistoryOut, UserIn, UserOut

router = APIRouter(prefix="/api/users", tags=["profiles"])


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Another profile already uses this email address.")


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.name, User.id)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, db: Session = Depends(get_db)):
    user = User(**body.model_dump())
    db.add(user)
    _commit_or_conflict(db)
    db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserOut)
def get_user(user: User = Depends(get_user_or_404)):
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(body: UserIn, user: User = Depends(get_user_or_404), db: Session = Depends(get_db)):
    for field, value in body.model_dump().items():
        setattr(user, field, value)  # assign new list/dict objects so JSON columns are saved
    _commit_or_conflict(db)
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user: User = Depends(get_user_or_404), db: Session = Depends(get_db)):
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{user_id}/activities", response_model=list[ActivityOut])
def list_activities(user: User = Depends(get_user_or_404), db: Session = Depends(get_db),
                    limit: int = Query(30, ge=1, le=200)):
    stmt = (select(Activity).where(Activity.user_id == user.id)
            .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(limit))
    return list(db.scalars(stmt))


@router.post("/{user_id}/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
def add_activity(body: ActivityIn, user: User = Depends(get_user_or_404), db: Session = Depends(get_db)):
    activity = Activity(user_id=user.id, **body.model_dump())
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


@router.delete("/{user_id}/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity(activity_id: int, user: User = Depends(get_user_or_404), db: Session = Depends(get_db)):
    activity = db.get(Activity, activity_id)
    if activity is None or activity.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Activity not found.")
    db.delete(activity)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{user_id}/queries", response_model=list[QueryHistoryOut])
def list_queries(user: User = Depends(get_user_or_404), db: Session = Depends(get_db),
                 limit: int = Query(30, ge=1, le=200)):
    stmt = (select(QueryLog).where(QueryLog.user_id == user.id)
            .order_by(QueryLog.created_at.desc(), QueryLog.id.desc()).limit(limit))
    return list(db.scalars(stmt))
