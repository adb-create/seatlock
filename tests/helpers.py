"""Shared test helpers (conftest.py has already configured the environment)."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.database import SessionLocal
from app.models import User


def make_user(client: TestClient, admin: bool = False) -> dict:
    """Register + log in a user. Returns {'id', 'email', 'headers'}."""
    email = f"user-{uuid.uuid4().hex[:10]}@test.dev"
    creds = {"email": email, "password": "password123"}
    r = client.post("/auth/register", json=creds)
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    if admin:
        with SessionLocal() as db:
            db.execute(update(User).where(User.id == user_id).values(is_admin=True))
            db.commit()
    token = client.post("/auth/login", json=creds).json()["access_token"]
    return {"id": user_id, "email": email, "headers": {"Authorization": f"Bearer {token}"}}


def make_event(client: TestClient, admin: dict, rows: int = 2, seats_per_row: int = 5) -> dict:
    r = client.post(
        "/events",
        headers=admin["headers"],
        json={
            "name": "Test Show",
            "venue": "Test Hall",
            "starts_at": "2030-01-01T18:00:00+05:30",
            "rows": rows,
            "seats_per_row": seats_per_row,
            "price": "250.00",
        },
    )
    assert r.status_code == 201, r.text
    event = r.json()
    event["seats"] = client.get(f"/events/{event['id']}/seats").json()
    return event


def key() -> str:
    return str(uuid.uuid4())
