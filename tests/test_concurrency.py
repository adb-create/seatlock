"""The tests an interviewer actually cares about.

Many threads, each with its own DB connection, hit the booking service at the
exact same moment (a Barrier releases them together).
"""

import random
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.models import Booking, BookingStatus, Seat, SeatStatus, User, booking_seats
from app.services import booking_service as svc
from tests.helpers import key, make_event, make_user

THREADS = 40  # stays below the test DB pool size (50)


def _run_race(user_ids, make_request):
    barrier = threading.Barrier(len(user_ids))

    def worker(user_id):
        with SessionLocal() as db:
            user = db.get(User, user_id)
            barrier.wait()  # everybody starts together
            try:
                make_request(db, user)
                return "won"
            except svc.SeatUnavailable:
                return "lost"
            except OperationalError as exc:  # deadlocks would show up here
                return f"db-error: {exc.orig!r}"

    with ThreadPoolExecutor(max_workers=len(user_ids)) as pool:
        return list(pool.map(worker, user_ids))


def test_last_seat_race_has_exactly_one_winner(client):
    admin = make_user(client, admin=True)
    event = make_event(client, admin, rows=1, seats_per_row=1)
    seat_id = event["seats"][0]["id"]
    users = [make_user(client)["id"] for _ in range(THREADS)]

    results = _run_race(
        users, lambda db, user: svc.hold_seats(db, user, event["id"], [seat_id], key())
    )

    assert results.count("won") == 1, results
    assert results.count("lost") == THREADS - 1, results
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Booking.id))) == 1
        assert db.get(Seat, seat_id).status == SeatStatus.HELD


def test_overlapping_multi_seat_requests_never_double_book_or_deadlock(client):
    """Users ask for random overlapping seat pairs, in random order.

    Locking seats in sorted id order is what rules out deadlocks here;
    any deadlock would surface as an OperationalError and fail the test.
    """
    admin = make_user(client, admin=True)
    event = make_event(client, admin, rows=1, seats_per_row=8)
    seat_ids = [s["id"] for s in event["seats"]]
    users = [make_user(client)["id"] for _ in range(THREADS)]
    rng = random.Random(42)
    requests = {u: rng.sample(seat_ids, 2) for u in users}  # random order on purpose

    results = _run_race(
        users,
        lambda db, user: svc.hold_seats(db, user, event["id"], requests[user.id], key()),
    )

    assert not [r for r in results if r.startswith("db-error")], results
    assert 1 <= results.count("won") <= len(seat_ids) // 2

    with SessionLocal() as db:
        # Every seat appears in at most one live booking.
        rows = db.execute(
            select(booking_seats.c.seat_id, func.count())
            .join(Booking, Booking.id == booking_seats.c.booking_id)
            .where(Booking.status == BookingStatus.PENDING)
            .group_by(booking_seats.c.seat_id)
        ).all()
        assert all(count == 1 for _, count in rows), rows
        # And the seat rows agree with the bookings.
        held = db.scalar(select(func.count(Seat.id)).where(Seat.status == SeatStatus.HELD))
        assert held == len(rows) == 2 * results.count("won")


def test_double_click_on_pay_charges_once(client):
    """Same user, same Idempotency-Key, many simultaneous confirm calls."""
    admin = make_user(client, admin=True)
    event = make_event(client, admin, rows=1, seats_per_row=1)
    user = make_user(client)
    booking = client.post(
        "/bookings",
        headers={**user["headers"], "Idempotency-Key": key()},
        json={"event_id": event["id"], "seat_ids": [event["seats"][0]["id"]]},
    ).json()
    pay_key = key()
    barrier = threading.Barrier(10)

    def pay(_):
        with SessionLocal() as db:
            u = db.get(User, user["id"])
            barrier.wait()
            return svc.confirm_booking(db, u, booking["id"], pay_key, "tok_success").payment_ref

    with ThreadPoolExecutor(max_workers=10) as pool:
        refs = set(pool.map(pay, range(10)))

    assert len(refs) == 1  # one payment reference no matter how many clicks
