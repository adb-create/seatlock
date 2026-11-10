from string import ascii_uppercase

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Event, Seat, SeatStatus, User
from app.schemas import EventCreate, EventOut, SeatOut
from app.services.booking_service import effective_status, utcnow

router = APIRouter(prefix="/events", tags=["events"])


def _seat_counts(db: Session, event_ids: list[int]) -> dict[int, tuple[int, int]]:
    """{event_id: (total_seats, available_seats)} in a single query."""
    if not event_ids:
        return {}
    now = utcnow()
    is_free = or_(
        Seat.status == SeatStatus.AVAILABLE,
        and_(Seat.status == SeatStatus.HELD, Seat.hold_expires_at <= now),
    )
    rows = db.execute(
        select(
            Seat.event_id,
            func.count(Seat.id),
            func.coalesce(func.sum(case((is_free, 1), else_=0)), 0),
        )
        .where(Seat.event_id.in_(event_ids))
        .group_by(Seat.event_id)
    ).all()
    return {event_id: (int(total), int(free)) for event_id, total, free in rows}


def _to_out(event: Event, counts: dict[int, tuple[int, int]]) -> EventOut:
    total, free = counts.get(event.id, (0, 0))
    return EventOut(
        id=event.id,
        name=event.name,
        venue=event.venue,
        starts_at=event.starts_at,
        total_seats=total,
        available_seats=free,
    )


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    body: EventCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> EventOut:
    event = Event(name=body.name, venue=body.venue, starts_at=body.starts_at)
    db.add(event)
    db.flush()
    db.add_all(
        Seat(
            event_id=event.id,
            row=row,
            number=number,
            label=f"{row}{number}",
            price=body.price,
            status=SeatStatus.AVAILABLE,
        )
        for row in ascii_uppercase[: body.rows]
        for number in range(1, body.seats_per_row + 1)
    )
    db.commit()
    return _to_out(event, _seat_counts(db, [event.id]))


@router.get("", response_model=list[EventOut])
def list_events(db: Session = Depends(get_db)) -> list[EventOut]:
    events = list(db.scalars(select(Event).order_by(Event.starts_at, Event.id)).all())
    counts = _seat_counts(db, [e.id for e in events])
    return [_to_out(e, counts) for e in events]


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, db: Session = Depends(get_db)) -> EventOut:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return _to_out(event, _seat_counts(db, [event.id]))


@router.get("/{event_id}/seats", response_model=list[SeatOut])
def seat_map(event_id: int, db: Session = Depends(get_db)) -> list[SeatOut]:
    if db.get(Event, event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    now = utcnow()
    seats = db.scalars(select(Seat).where(Seat.event_id == event_id).order_by(Seat.id)).all()
    return [
        SeatOut(
            id=s.id,
            label=s.label,
            row=s.row,
            number=s.number,
            price=s.price,
            status=effective_status(s, now),
        )
        for s in seats
    ]
