"""Load two demo profiles with realistic activity so you can try the rewriter immediately.

Usage (from the project root):
    python -m scripts.seed           # adds demo profiles if they don't exist yet
    python -m scripts.seed --reset   # deletes ALL data first, then adds the demo profiles
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, create_db_engine, utcnow
from app.models import Activity, QueryLog, User

DEMO_USERS = [
    {
        "profile": {
            "name": "Priya Sharma",
            "email": "priya.demo@example.com",
            "role": "Data analyst",
            "location": "Hyderabad, India",
            "interests": ["SQL", "Power BI", "Python", "data visualization"],
            "preferences": {"level": "intermediate", "format": "video courses"},
        },
        "activity": [
            ("view", "Advanced SQL window functions course", 2),
            ("search", "power bi dax measures", 5),
            ("bookmark", "Python pandas for analysts tutorial", 20),
            ("enroll", "Data visualization fundamentals", 72),
        ],
    },
    {
        "profile": {
            "name": "Arjun Mehta",
            "email": "arjun.demo@example.com",
            "role": "Backend developer",
            "location": "Bengaluru, India",
            "interests": ["Java", "Spring Boot", "microservices", "AWS"],
            "preferences": {"level": "advanced", "format": "documentation"},
        },
        "activity": [
            ("view", "Spring Boot microservices with Kafka guide", 1),
            ("download", "AWS ECS deployment checklist", 26),
            ("search", "java 21 virtual threads", 50),
        ],
    },
]


def seed(reset: bool) -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        if reset:
            db.execute(delete(QueryLog))
            db.execute(delete(Activity))
            db.execute(delete(User))
            db.commit()
            print("Deleted all existing data.")

        now = utcnow()
        for demo in DEMO_USERS:
            email = demo["profile"]["email"]
            if db.scalar(select(User).where(User.email == email)):
                print(f"Skipped {demo['profile']['name']} (already exists).")
                continue
            user = User(**demo["profile"])
            db.add(user)
            db.flush()
            for event_type, content, hours_ago in demo["activity"]:
                db.add(Activity(user_id=user.id, event_type=event_type, content=content,
                                created_at=now - timedelta(hours=hours_ago)))
            print(f"Added {user.name} with {len(demo['activity'])} activities.")
        db.commit()
    engine.dispose()
    print("Done. Start the app and pick a profile from the top-right menu.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete all data before seeding")
    seed(parser.parse_args().reset)
