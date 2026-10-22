"""Defines the Transaction class, which represents a single financial transaction.

Design notes
------------
* Money is stored as a *signed integer* number of cents (e.g. $12.34 -> 1234).
  Floats are never used for money because binary floating point cannot represent
  most decimal values exactly (0.1 + 0.2 != 0.3), which causes rounding drift.
    - Positive amounts are money coming IN  (income, refunds).
    - Negative amounts are money going OUT (purchases, bills).
* Every transaction gets a UUID (Universally Unique Identifier) as its ID.
  A UUID4 is 122 random bits, so two transactions will practically never
  collide, and IDs can be generated without asking a database for the "next" ID.
* A dataclass is used so the attributes (id, amount, description, category,
  date) are plain public fields that can be read after the object is created,
  e.g. ``txn.amount`` or ``txn.category``.

Libraries used: standard library only (dataclasses, datetime, uuid).
"""

from dataclasses import dataclass, field
from datetime import date as Date
from uuid import UUID, uuid4


@dataclass
class Transaction:
    """A single transaction, such as a purchase, paycheck, or transfer.

    Attributes:
        amount: Signed amount in cents. Positive = income, negative = expense.
        description: Human-readable text, e.g. "Coffee at Blue Bottle".
        category: Grouping label, e.g. "Food", "Rent", "Salary".
        date: The calendar date the transaction occurred.
        id: Unique identifier. Generated automatically with uuid4() if not
            supplied. It is declared last so callers can omit it; an existing
            ID can still be passed in (useful later when loading saved data
            or importing from Plaid).

    Example:
        >>> from datetime import date
        >>> txn = Transaction(amount=-450, description="Coffee",
        ...                   category="Food", date=date(2026, 10, 21))
        >>> txn.amount
        -450
    """

    amount: int
    description: str
    category: str
    date: Date
    # default_factory runs uuid4() separately for each new object, so every
    # Transaction gets its own fresh UUID (a plain default would be shared).
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate the fields right after the dataclass __init__ assigns them.

        Dataclasses do not enforce type hints at runtime, so we check the
        important ones here. Catching bad data at creation time is much easier
        to debug than finding it later in a calculation.

        Raises:
            TypeError: If a field has the wrong type.
        """
        # bool is a subclass of int in Python (True == 1), so it must be
        # rejected explicitly or Transaction(amount=True, ...) would be allowed.
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise TypeError(
                f"amount must be an int number of cents, got {type(self.amount).__name__}"
            )
        if not isinstance(self.description, str):
            raise TypeError("description must be a str")
        if not isinstance(self.category, str):
            raise TypeError("category must be a str")
        if not isinstance(self.date, Date):
            raise TypeError("date must be a datetime.date")
        if not isinstance(self.id, UUID):
            raise TypeError("id must be a uuid.UUID")
