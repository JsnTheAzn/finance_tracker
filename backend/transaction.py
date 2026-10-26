"""Pydantic models for a single transaction. See docs/phase1.md and docs/phase4.md."""

from datetime import date as Date
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _parse_iso_date(value: Any) -> Any:
    """Turn an ISO "YYYY-MM-DD" string into a date; leave anything else for strict checking."""
    if isinstance(value, str):
        return Date.fromisoformat(value)
    return value


# A date that may also arrive as an ISO string (JSON has no date type).
IsoDate = Annotated[Date, BeforeValidator(_parse_iso_date)]


class TransactionCreate(BaseModel):
    """The fields a client sends to create a transaction. Amounts are signed cents."""

    model_config = ConfigDict(strict=True)

    amount: int = Field(description="Signed amount in cents; negative = money out")
    description: str = Field(description="What the transaction was for")
    category: str = Field(description="Grouping label, e.g. Food or Rent")
    date: IsoDate = Field(description="Date the transaction occurred (YYYY-MM-DD)")


class Transaction(TransactionCreate):
    """A stored transaction: the TransactionCreate fields plus server-side fields."""

    id: UUID = Field(default_factory=uuid4, description="Unique transaction ID")
    # Bank-only fields: not on TransactionCreate, so API clients can't set them.
    external_id: str | None = Field(
        default=None,
        description="ID from the source bank (Plaid's transaction_id); null if added by hand",
    )
    pending: bool = Field(
        default=False,
        description="True if the bank hasn't posted this transaction yet (amount may change)",
    )

    @classmethod
    def from_create(cls, data: TransactionCreate) -> "Transaction":
        """Build a stored Transaction, with a new UUID, from client data."""
        return cls(**data.model_dump())


def format_cents(cents: int) -> str:
    """Format signed cents as dollars, e.g. -120000 -> '-$1,200.00'."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"
