"""Finance Tracker API (FastAPI) plus the Phase 1 demo. See docs/phase1.md and docs/phase2.md.

    .venv/bin/python main.py          # start the API at http://127.0.0.1:8000/docs
    .venv/bin/python main.py --demo   # run the Phase 1 print demo
"""

import sys
from datetime import date
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status

from backend.transaction import Transaction, TransactionCreate, format_cents
from backend.transaction_manager import TransactionManager

app = FastAPI(
    title="Finance Tracker API",
    description="Basic transaction endpoints for the Finance Tracker backend.",
)

_manager = TransactionManager()


def get_manager() -> TransactionManager:
    """The manager endpoints use. Tests override this; later it can return a DB-backed one."""
    return _manager


def _not_found(transaction_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Transaction {transaction_id} not found",
    )


@app.get(
    "/transactions",
    response_model=list[Transaction],
    operation_id="getAllTransaction",
    summary="List all transactions",
)
def get_all_transactions(
    manager: TransactionManager = Depends(get_manager),
) -> list[Transaction]:
    """Return every stored transaction, in the order they were added."""
    return manager.get_all_transactions()


@app.get(
    "/transactions/{transaction_id}",
    response_model=Transaction,
    operation_id="getTransaction",
    summary="Get one transaction by ID",
)
def get_transaction(
    transaction_id: UUID,
    manager: TransactionManager = Depends(get_manager),
) -> Transaction:
    """Return the transaction with the given UUID, or 404."""
    try:
        return manager.get_transaction(transaction_id)
    except KeyError:
        raise _not_found(transaction_id) from None


@app.post(
    "/transactions",
    response_model=Transaction,
    status_code=status.HTTP_201_CREATED,
    operation_id="addTransaction",
    summary="Add a new transaction",
)
def add_transaction(
    data: TransactionCreate,
    manager: TransactionManager = Depends(get_manager),
) -> Transaction:
    """Create a transaction from the body; the server assigns the ID."""
    transaction = Transaction.from_create(data)
    manager.add_transaction(transaction)
    return transaction


@app.delete(
    "/transactions/{transaction_id}",
    response_model=Transaction,
    operation_id="deleteTransaction",
    summary="Delete a transaction by ID",
)
def delete_transaction(
    transaction_id: UUID,
    manager: TransactionManager = Depends(get_manager),
) -> Transaction:
    """Delete the transaction with the given UUID and return it, or 404."""
    try:
        return manager.delete_transaction(transaction_id)
    except KeyError:
        raise _not_found(transaction_id) from None


def print_transaction(txn: Transaction) -> None:
    print(
        f"  {txn.id} | {txn.date} | {txn.category:<7} | "
        f"{txn.description:<10} | {format_cents(txn.amount)}"
    )


def run_demo() -> None:
    """Phase 1 demo: add three transactions, look one up, net change, delete one."""
    manager = TransactionManager()
    print("Step 1: Created an empty TransactionManager")
    print(f"  Stored transactions: {len(manager)}\n")

    paycheck = Transaction(amount=250000, description="Paycheck", category="Salary", date=date(2026, 10, 15))
    rent = Transaction(amount=-120000, description="Rent", category="Housing", date=date(2026, 10, 1))
    coffee = Transaction(amount=-450, description="Coffee", category="Food", date=date(2026, 10, 21))

    paycheck_id = manager.add_transaction(paycheck)
    rent_id = manager.add_transaction(rent)
    coffee_id = manager.add_transaction(coffee)

    print("Step 2: Added three transactions")
    for txn_id in (paycheck_id, rent_id, coffee_id):
        print_transaction(manager.get_transaction(txn_id))
    print(f"  Stored transactions: {len(manager)}\n")

    found = manager.get_transaction(rent_id)
    print(f"Step 3: Retrieved transaction by UUID {rent_id}")
    print_transaction(found)
    print(f"  Same object that was added? {found is rent}\n")

    net = manager.calculate_net_change()
    print("Step 4: Calculated net change")
    print(f"  Net change: {net} cents ({format_cents(net)})\n")

    removed = manager.delete_transaction(coffee_id)
    print("Step 5: Deleted a transaction")
    print_transaction(removed)
    print(f"  Stored transactions: {len(manager)}")
    try:
        manager.get_transaction(coffee_id)
        print("  ERROR: deleted transaction is still retrievable")
    except KeyError as error:
        print(f"  Lookup after delete raises KeyError: {error}")

    net_after = manager.calculate_net_change()
    print(f"  Net change after delete: {net_after} cents ({format_cents(net_after)})")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    else:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8000)
