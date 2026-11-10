"""The heart of the project: holding, confirming and releasing seats safely.

Concurrency rules (read these before changing anything):

1. Seats are locked with SELECT ... FOR UPDATE before we decide anything.
   Two transactions that want the same seat run one after the other, so the
   second one always sees the first one's result. No double booking.

2. Locks are always taken in ascending seat-id order. If user X wants
   seats [5, 6] and user Y wants [6, 5], both lock 5 first, so they can
   never wait on each other in a cycle (no deadlocks).

3. Lock order between tables is always booking -> seats. hold_seats() only
   locks seats, so no code path can lock seats and then wait on a booking.

4. Postgres is the single source of truth for holds (seat.hold_expires_at).
   An expired hold is treated as free immediately, even before the background
   sweeper has cleaned it up, so users never wait on the sweeper.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, BookingStatus, Event, Seat, SeatStatus, User


# ---------- errors (mapped to HTTP responses in app/main.py) ----------
class BookingError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(BookingError):
    status_code = 404


class SeatUnavailable(BookingError):
    status_code = 409


class InvalidState(BookingError):
    status_code = 409


class HoldExpired(BookingError):
    status_code = 410


class PaymentFailed(BookingError):
    status_code = 402


class IdempotencyMismatch(BookingError):
    status_code = 422


# ---------- helpers ----------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def effective_status(seat: Seat, now: datetime) -> SeatStatus:
    """What a customer should see: a hold that has run out counts as available."""
    if (
        seat.status == SeatStatus.HELD
        and seat.hold_expires_at is not None
        and seat.hold_expires_at <= now
    ):
        return SeatStatus.AVAILABLE
    return seat.status


def _lock_seats_of(db: Session, booking: Booking) -> list[Seat]:
    """Lock the seats this booking currently owns (in id order)."""
    stmt = (
        select(Seat)
        .where(Seat.booking_id == booking.id)
        .order_by(Seat.id)
        .with_for_update()
    )
    return list(db.scalars(stmt).all())


def _release(db: Session, booking: Booking, new_status: BookingStatus) -> None:
    """Give the booking's seats back and move the booking to a final status."""
    for seat in _lock_seats_of(db, booking):
        seat.status = SeatStatus.AVAILABLE
        seat.booking_id = None
        seat.hold_expires_at = None
    booking.status = new_status


def _charge(amount, payment_token: str) -> str:
    """Mock payment gateway. Returns a payment reference or raises PaymentFailed."""
    if payment_token == "tok_fail":
        raise PaymentFailed("Payment declined by the (mock) gateway")
    return f"pay_{uuid.uuid4().hex[:16]}"


# ---------- use cases ----------
def hold_seats(
    db: Session,
    user: User,
    event_id: int,
    seat_ids: Sequence[int],
    idempotency_key: str,
) -> tuple[Booking, bool]:
    """Hold seats for HOLD_TTL_SECONDS. Returns (booking, created).

    created=False means this was a retry with an idempotency key we have
    already seen, and the original booking is returned unchanged.
    """
    wanted = sorted(set(seat_ids))  # sorted -> consistent lock order
    if not wanted:
        raise BookingError("Choose at least one seat")
    if len(wanted) > settings.max_seats_per_booking:
        raise BookingError(f"You can book at most {settings.max_seats_per_booking} seats at once")

    if db.get(Event, event_id) is None:
        raise NotFound("Event not found")

    # 1) Lock the requested seats. Competing requests queue up here.
    seats = list(
        db.scalars(
            select(Seat)
            .where(Seat.id.in_(wanted), Seat.event_id == event_id)
            .order_by(Seat.id)
            .with_for_update()
        ).all()
    )
    if len(seats) != len(wanted):
        db.rollback()
        raise NotFound("One or more seats do not belong to this event")

    # 2) Idempotency check *after* locking: if a retry of this same request
    #    got the locks first and committed, we see its booking now.
    existing = db.scalar(
        select(Booking).where(Booking.user_id == user.id, Booking.hold_key == idempotency_key)
    )
    if existing is not None:
        db.rollback()
        return _replay(existing, event_id, wanted), False

    # 3) Every seat must be free (or have an expired hold).
    now = utcnow()
    taken = [s.label for s in seats if effective_status(s, now) != SeatStatus.AVAILABLE]
    if taken:
        db.rollback()  # release the locks straight away
        raise SeatUnavailable(f"Seats already taken: {', '.join(taken)}")

    # 4) Create the booking and mark the seats as held by it.
    expires_at = now + timedelta(seconds=settings.hold_ttl_seconds)
    booking = Booking(
        user_id=user.id,
        event_id=event_id,
        status=BookingStatus.PENDING,
        total_amount=sum((s.price for s in seats), start=0),
        expires_at=expires_at,
        hold_key=idempotency_key,
    )
    booking.seats = seats
    db.add(booking)
    db.flush()  # assigns booking.id

    for seat in seats:
        seat.status = SeatStatus.HELD
        seat.booking_id = booking.id
        seat.hold_expires_at = expires_at

    try:
        db.commit()
    except IntegrityError:
        # Same key used concurrently for a *different* seat set: the unique
        # constraint on (user_id, hold_key) stopped the second insert.
        db.rollback()
        existing = db.scalar(
            select(Booking).where(Booking.user_id == user.id, Booking.hold_key == idempotency_key)
        )
        if existing is None:
            raise
        return _replay(existing, event_id, wanted), False

    return booking, True


def _replay(existing: Booking, event_id: int, wanted: list[int]) -> Booking:
    if existing.event_id != event_id or sorted(s.id for s in existing.seats) != wanted:
        raise IdempotencyMismatch(
            "This Idempotency-Key was already used for a different request"
        )
    return existing


def confirm_booking(
    db: Session,
    user: User,
    booking_id: int,
    idempotency_key: str,
    payment_token: str,
) -> Booking:
    """Pay for a held booking. Safe to retry with the same Idempotency-Key."""
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if booking is None or booking.user_id != user.id:
        raise NotFound("Booking not found")

    if booking.status == BookingStatus.CONFIRMED:
        if booking.confirm_key == idempotency_key:
            db.rollback()
            return booking  # retry of a payment that already went through
        raise InvalidState("Booking is already confirmed")
    if booking.status != BookingStatus.PENDING:
        raise InvalidState(f"Booking is {booking.status.value}")

    now = utcnow()
    if booking.expires_at <= now:
        _release(db, booking, BookingStatus.EXPIRED)
        db.commit()
        raise HoldExpired("Your hold expired before payment, please choose seats again")

    seats = _lock_seats_of(db, booking)
    if len(seats) != len(booking.seats):
        # Edge case: the hold expired a moment ago and someone else already
        # grabbed a seat. Never confirm a booking that lost a seat.
        _release(db, booking, BookingStatus.EXPIRED)
        db.commit()
        raise HoldExpired("Your hold expired before payment, please choose seats again")

    # Note: a real system would not call a payment provider while holding row
    # locks (see README, "What would you change in production?").
    booking.payment_ref = _charge(booking.total_amount, payment_token)
    booking.status = BookingStatus.CONFIRMED
    booking.confirm_key = idempotency_key
    booking.confirmed_at = now
    for seat in seats:
        seat.status = SeatStatus.BOOKED
        seat.hold_expires_at = None

    db.commit()
    return booking


def cancel_booking(db: Session, user: User, booking_id: int) -> Booking:
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if booking is None or booking.user_id != user.id:
        raise NotFound("Booking not found")
    if booking.status not in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
        raise InvalidState(f"Booking is already {booking.status.value}")

    _release(db, booking, BookingStatus.CANCELLED)
    db.commit()
    return booking


def expire_stale_holds(db: Session, batch_size: int = 500) -> int:
    """Background job: mark overdue PENDING bookings EXPIRED and free their seats.

    SKIP LOCKED means that if a user is confirming that exact booking right
    now, we leave it alone instead of waiting, so the sweeper never slows
    down real requests (and several sweepers could run side by side).
    """
    now = utcnow()
    stale = list(
        db.scalars(
            select(Booking)
            .where(Booking.status == BookingStatus.PENDING, Booking.expires_at <= now)
            .order_by(Booking.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    for booking in stale:
        _release(db, booking, BookingStatus.EXPIRED)
    db.commit()
    return len(stale)
