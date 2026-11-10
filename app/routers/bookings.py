from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, rate_limited_user
from app.models import Booking, User
from app.schemas import BookingOut, ConfirmRequest, HoldRequest
from app.services import booking_service as svc

router = APIRouter(prefix="/bookings", tags=["bookings"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=100,
        description="A unique value (e.g. a UUID) per user action. Retrying with the "
        "same key never creates a second booking or a second payment.",
    ),
]


@router.post(
    "",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Retry of an earlier request; original booking returned"},
        409: {"description": "At least one seat is already taken"},
        429: {"description": "Rate limit hit"},
    },
)
def hold_seats(
    body: HoldRequest,
    idempotency_key: IdempotencyKey,
    response: Response,
    user: User = Depends(rate_limited_user),
    db: Session = Depends(get_db),
) -> Booking:
    """Hold seats for a few minutes while the user pays."""
    booking, created = svc.hold_seats(db, user, body.event_id, body.seat_ids, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return booking


@router.post(
    "/{booking_id}/confirm",
    response_model=BookingOut,
    responses={
        402: {"description": "Payment declined"},
        410: {"description": "The hold expired"},
    },
)
def confirm(
    booking_id: int,
    idempotency_key: IdempotencyKey,
    body: ConfirmRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Booking:
    """Pay for a held booking (mock payment)."""
    token = body.payment_token if body else "tok_success"
    return svc.confirm_booking(db, user, booking_id, idempotency_key, token)


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel(
    booking_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Booking:
    return svc.cancel_booking(db, user, booking_id)


@router.get("", response_model=list[BookingOut])
def my_bookings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Booking]:
    stmt = select(Booking).where(Booking.user_id == user.id).order_by(Booking.id.desc())
    return list(db.scalars(stmt).all())


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(
    booking_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        raise svc.NotFound("Booking not found")
    return booking
