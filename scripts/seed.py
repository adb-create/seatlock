"""Create an admin account and a sample event.

    python -m scripts.seed

Admin login comes from ADMIN_EMAIL / ADMIN_PASSWORD (defaults below).
Safe to run more than once.
"""

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from string import ascii_uppercase

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Event, Seat, SeatStatus, User
from app.security import hash_password

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@seatlock.dev").lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin12345")


def main() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if admin is None:
            db.add(User(email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD), is_admin=True))
            print(f"Created admin {ADMIN_EMAIL}")
        else:
            admin.is_admin = True
            print(f"Admin {ADMIN_EMAIL} already exists")

        if db.scalar(select(Event).limit(1)) is None:
            event = Event(
                name="Arijit Singh Live",
                venue="Netaji Indoor Stadium, Kolkata",
                starts_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
            db.add(event)
            db.flush()
            db.add_all(
                Seat(
                    event_id=event.id,
                    row=row,
                    number=n,
                    label=f"{row}{n}",
                    price=Decimal("1499.00") if row in "AB" else Decimal("799.00"),
                    status=SeatStatus.AVAILABLE,
                )
                for row in ascii_uppercase[:10]
                for n in range(1, 21)
            )
            print(f"Created sample event #{event.id} with 200 seats")
        db.commit()


if __name__ == "__main__":
    main()
