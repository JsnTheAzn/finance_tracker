"""Defines the Pydantic models that represent a single financial transaction.

Design notes
------------
* Pydantic models describe *what the API expects*. Each field has a type, and
  Pydantic checks incoming data against those types when a model is created,
  raising ``pydantic.ValidationError`` if anything is wrong. FastAPI (Phase 2)
  will use these same models to validate request bodies and to generate the
  API docs automatically.
* There are two models:
    - ``TransactionCreate``: the fields a client sends to create a transaction
      (amount, description, category, date). The client does NOT choose the ID.
    - ``Transaction``: everything in TransactionCreate plus a server-generated
      ``id``. This is what gets stored and returned.
  Splitting them this way means the API can never be tricked into accepting a
  caller-chosen ID on create, while stored transactions always have one.
* Money is stored as a *signed integer* number of cents (e.g. $12.34 -> 1234).
  Floats are never used for money because binary floating point cannot represent
  most decimal values exactly (0.1 + 0.2 != 0.3), which causes rounding drift.
    - Positive amounts are money coming IN  (income, refunds).
    - Negative amounts are money going OUT (purchases, bills).
* Every transaction gets a UUID (Universally Unique Identifier) as its ID.
  A UUID4 is 122 random bits, so two transactions will practically never
  collide, and IDs can be generated without asking a database for the "next" ID.
* Strict mode is turned on (see ``model_config`` below). By default Pydantic
  "helpfully" converts values, e.g. the string "450" or the bool True into an
  int. For money that hides bugs, so strict mode rejects wrong types instead.
  Strict mode still accepts ISO date strings ("2026-10-21") and UUID strings
  when parsing JSON, which is exactly what the API will receive.

Libraries used: pydantic (installed with FastAPI), plus the standard library
(datetime, uuid).
"""

from datetime import date as Date
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class TransactionCreate(BaseModel):
    """The data a client provides to create a transaction.

    Attributes:
        amount: Signed amount in cents. Positive = income, negative = expense.
        description: Human-readable text, e.g. "Coffee at Blue Bottle".
        category: Grouping label, e.g. "Food", "Rent", "Salary".
        date: The calendar date the transaction occurred.

    Example:
        >>> from datetime import date
        >>> data = TransactionCreate(amount=-450, description="Coffee",
        ...                          category="Food", date=date(2026, 10, 21))
        >>> data.amount
        -450
    """

    # strict=True: no silent type conversion (e.g. 4.5 or "450" for amount).
    model_config = ConfigDict(strict=True)

    # Field(...) attaches a description that FastAPI will show in its API docs.
    amount: int = Field(description="Signed amount in cents; negative = money out")
    description: str = Field(description="What the transaction was for")
    category: str = Field(description="Grouping label, e.g. Food or Rent")
    date: Date = Field(description="Date the transaction occurred")


class Transaction(TransactionCreate):
    """A stored transaction: the TransactionCreate fields plus a unique ID.

    Inheriting from TransactionCreate means the four data fields (and strict
    mode) are defined in one place and reused here.

    Attributes:
        id: Unique identifier. Generated automatically with uuid4() if not
            supplied. An existing ID can still be passed in (useful later when
            loading saved data from a database or importing from Plaid).

    Example:
        >>> from datetime import date
        >>> txn = Transaction(amount=-450, description="Coffee",
        ...                   category="Food", date=date(2026, 10, 21))
        >>> txn.category
        'Food'
        >>> type(txn.id).__name__
        'UUID'
    """

    # default_factory runs uuid4() separately for each new object, so every
    # Transaction gets its own fresh UUID (a plain default would be shared).
    id: UUID = Field(default_factory=uuid4, description="Unique transaction ID")

    @classmethod
    def from_create(cls, data: TransactionCreate) -> "Transaction":
        """Build a stored Transaction (with a new UUID) from client data.

        This is how the API layer will turn a validated request body into a
        Transaction ready to hand to TransactionManager.

        Args:
            data: The validated fields sent by the client.

        Returns:
            A new Transaction with the same fields and a freshly generated id.
        """
        # model_dump() turns the model into a plain dict of its fields, which
        # is then unpacked into the Transaction constructor.
        return cls(**data.model_dump())
