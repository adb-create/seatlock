from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.database import SessionLocal
from app.models import Booking, BookingStatus, Seat
from app.services.booking_service import expire_stale_holds
from tests.helpers import key, make_event, make_user


def hold(client, user, event, seat_ids, idem=None):
    return client.post(
        "/bookings",
        headers={**user["headers"], "Idempotency-Key": idem or key()},
        json={"event_id": event["id"], "seat_ids": seat_ids},
    )


def confirm(client, user, booking_id, idem=None, token="tok_success"):
    return client.post(
        f"/bookings/{booking_id}/confirm",
        headers={**user["headers"], "Idempotency-Key": idem or key()},
        json={"payment_token": token},
    )


def seat_status(client, event, seat_id):
    seats = client.get(f"/events/{event['id']}/seats").json()
    return next(s["status"] for s in seats if s["id"] == seat_id)


def force_expire(booking_id):
    """Pretend the hold timer ran out."""
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    with SessionLocal() as db:
        db.execute(update(Booking).where(Booking.id == booking_id).values(expires_at=past))
        db.execute(update(Seat).where(Seat.booking_id == booking_id).values(hold_expires_at=past))
        db.commit()


def setup(client, rows=2, per_row=5):
    admin = make_user(client, admin=True)
    return make_event(client, admin, rows, per_row)


# ---------- happy path ----------
def test_hold_then_confirm(client):
    event = setup(client)
    user = make_user(client)
    s1, s2 = event["seats"][0]["id"], event["seats"][1]["id"]

    r = hold(client, user, event, [s2, s1])
    assert r.status_code == 201, r.text
    booking = r.json()
    assert booking["status"] == "PENDING"
    assert booking["total_amount"] == "500.00"
    assert [s["label"] for s in booking["seats"]] == ["A1", "A2"]
    assert seat_status(client, event, s1) == "HELD"
    assert client.get(f"/events/{event['id']}").json()["available_seats"] == 8

    r = confirm(client, user, booking["id"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED"
    assert r.json()["payment_ref"].startswith("pay_")
    assert seat_status(client, event, s1) == "BOOKED"


def test_cannot_hold_a_taken_seat(client):
    event = setup(client)
    alice, bob = make_user(client), make_user(client)
    seat = event["seats"][0]["id"]

    assert hold(client, alice, event, [seat]).status_code == 201
    r = hold(client, bob, event, [seat, event["seats"][1]["id"]])
    assert r.status_code == 409
    assert "A1" in r.json()["detail"]
    # All-or-nothing: Bob's failed request must not have held A2 either.
    assert seat_status(client, event, event["seats"][1]["id"]) == "AVAILABLE"


def test_seat_from_another_event_is_rejected(client):
    admin = make_user(client, admin=True)
    e1, e2 = make_event(client, admin), make_event(client, admin)
    user = make_user(client)
    assert hold(client, user, e1, [e2["seats"][0]["id"]]).status_code == 404


# ---------- idempotency ----------
def test_retrying_a_hold_returns_the_same_booking(client):
    event = setup(client)
    user = make_user(client)
    seat = event["seats"][0]["id"]
    idem = key()

    first = hold(client, user, event, [seat], idem)
    retry = hold(client, user, event, [seat], idem)
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert len(client.get("/bookings", headers=user["headers"]).json()) == 1


def test_reusing_a_key_for_different_seats_is_an_error(client):
    event = setup(client)
    user = make_user(client)
    idem = key()
    assert hold(client, user, event, [event["seats"][0]["id"]], idem).status_code == 201
    assert hold(client, user, event, [event["seats"][1]["id"]], idem).status_code == 422


def test_retrying_a_payment_does_not_charge_twice(client):
    event = setup(client)
    user = make_user(client)
    booking_id = hold(client, user, event, [event["seats"][0]["id"]]).json()["id"]
    idem = key()

    first = confirm(client, user, booking_id, idem)
    retry = confirm(client, user, booking_id, idem)
    assert first.status_code == retry.status_code == 200
    assert retry.json()["payment_ref"] == first.json()["payment_ref"]

    # A *new* payment attempt on a paid booking is refused.
    assert confirm(client, user, booking_id).status_code == 409


# ---------- payment failure / expiry / cancel ----------
def test_declined_payment_keeps_the_hold(client):
    event = setup(client)
    user = make_user(client)
    seat = event["seats"][0]["id"]
    booking_id = hold(client, user, event, [seat]).json()["id"]

    assert confirm(client, user, booking_id, token="tok_fail").status_code == 402
    assert client.get(f"/bookings/{booking_id}", headers=user["headers"]).json()["status"] == "PENDING"
    assert seat_status(client, event, seat) == "HELD"
    assert confirm(client, user, booking_id).status_code == 200  # card works the second time


def test_expired_hold_cannot_be_paid_and_seat_is_free_again(client):
    event = setup(client)
    alice, bob = make_user(client), make_user(client)
    seat = event["seats"][0]["id"]
    booking_id = hold(client, alice, event, [seat]).json()["id"]

    force_expire(booking_id)
    assert seat_status(client, event, seat) == "AVAILABLE"  # visible immediately

    assert confirm(client, alice, booking_id).status_code == 410
    assert client.get(f"/bookings/{booking_id}", headers=alice["headers"]).json()["status"] == "EXPIRED"
    assert hold(client, bob, event, [seat]).status_code == 201


def test_expired_seat_can_be_taken_before_the_sweeper_runs(client):
    event = setup(client)
    alice, bob = make_user(client), make_user(client)
    seat = event["seats"][0]["id"]
    alice_booking = hold(client, alice, event, [seat]).json()["id"]
    force_expire(alice_booking)

    bob_booking = hold(client, bob, event, [seat])
    assert bob_booking.status_code == 201
    # Alice's late payment must fail: her seat now belongs to Bob.
    assert confirm(client, alice, alice_booking).status_code == 410
    assert confirm(client, bob, bob_booking.json()["id"]).status_code == 200


def test_sweeper_releases_stale_holds(client):
    event = setup(client)
    user = make_user(client)
    seat = event["seats"][0]["id"]
    booking_id = hold(client, user, event, [seat]).json()["id"]
    force_expire(booking_id)

    with SessionLocal() as db:
        assert expire_stale_holds(db) == 1
        assert db.get(Booking, booking_id).status == BookingStatus.EXPIRED
        assert db.get(Seat, seat).booking_id is None
    with SessionLocal() as db:
        assert expire_stale_holds(db) == 0  # nothing left to do


def test_cancel_frees_the_seat(client):
    event = setup(client)
    alice, bob = make_user(client), make_user(client)
    seat = event["seats"][0]["id"]
    booking_id = hold(client, alice, event, [seat]).json()["id"]
    confirm(client, alice, booking_id)

    r = client.post(f"/bookings/{booking_id}/cancel", headers=alice["headers"])
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    assert [s["label"] for s in r.json()["seats"]] == ["A1"]  # history is kept
    assert hold(client, bob, event, [seat]).status_code == 201
    assert client.post(f"/bookings/{booking_id}/cancel", headers=alice["headers"]).status_code == 409


# ---------- security ----------
def test_users_cannot_touch_each_others_bookings(client):
    event = setup(client)
    alice, mallory = make_user(client), make_user(client)
    booking_id = hold(client, alice, event, [event["seats"][0]["id"]]).json()["id"]

    assert client.get(f"/bookings/{booking_id}", headers=mallory["headers"]).status_code == 404
    assert confirm(client, mallory, booking_id).status_code == 404
    assert client.post(f"/bookings/{booking_id}/cancel", headers=mallory["headers"]).status_code == 404


def test_rate_limit(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "hold_rate_limit_per_minute", 2)
    event = setup(client)
    user = make_user(client)
    codes = [hold(client, user, event, [event["seats"][i]["id"]]).status_code for i in range(3)]
    assert codes == [201, 201, 429]


def test_max_seats_per_booking(client):
    event = setup(client, rows=2, per_row=10)
    user = make_user(client)
    r = hold(client, user, event, [s["id"] for s in event["seats"][:11]])
    assert r.status_code == 400
