from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import BookingStatus, SeatStatus

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ---------- auth ----------
class Credentials(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255, examples=["riya@example.com"])
    password: str = Field(min_length=8, max_length=128, examples=["supersecret123"])

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_admin: bool


# ---------- events & seats ----------
class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, examples=["Coldplay — Kolkata"])
    venue: str = Field(min_length=1, max_length=200, examples=["Eden Gardens"])
    starts_at: datetime
    rows: int = Field(ge=1, le=26, description="Rows are labelled A, B, C ...")
    seats_per_row: int = Field(ge=1, le=50)
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2, examples=["499.00"])


class EventOut(BaseModel):
    id: int
    name: str
    venue: str
    starts_at: datetime
    total_seats: int
    available_seats: int


class SeatOut(BaseModel):
    id: int
    label: str
    row: str
    number: int
    price: Decimal
    status: SeatStatus


class SeatBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str


# ---------- bookings ----------
class HoldRequest(BaseModel):
    event_id: int
    seat_ids: list[int] = Field(min_length=1, examples=[[1, 2]])


class ConfirmRequest(BaseModel):
    # Mock payment gateway: "tok_fail" simulates a declined card.
    payment_token: str = Field(default="tok_success", examples=["tok_success"])


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    status: BookingStatus
    total_amount: Decimal
    expires_at: datetime
    confirmed_at: datetime | None
    payment_ref: str | None
    seats: list[SeatBrief]
