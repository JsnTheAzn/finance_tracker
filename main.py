"""Entry point for the Finance Tracker backend: the FastAPI app plus the Phase 1 demo.

Phase 2: REST API
-----------------
This module creates a FastAPI application (``app``) that exposes the
TransactionManager over HTTP so the frontend (and later the Plaid integration)
can talk to the backend. The four basic actions are:

    Action (operation_id)   HTTP method + path                   Success code
    ---------------------   ----------------------------------   ------------
    getAllTransaction       GET    /transactions                 200 OK
    getTransaction          GET    /transactions/{id}            200 OK
    addTransaction          POST   /transactions                 201 Created
    deleteTransaction       DELETE /transactions/{id}            200 OK

The camelCase action names are set as each route's ``operation_id``. That is
the name shown in the interactive docs and the name a generated frontend
client would use (e.g. ``api.addTransaction(...)``). The Python functions
themselves use snake_case, following normal Python style.

Errors:
    * An unknown transaction ID returns ``404 Not Found``.
    * An ID that is not a valid UUID, or a body with missing/wrong-typed
      fields (e.g. ``"amount": 4.5``), returns ``422 Unprocessable Content``.
      FastAPI produces these automatically from the Pydantic models.

Storage is still the in-memory TransactionManager, so data is lost when the
server stops. (A database comes in a later phase.)

How to run
----------
Start the API server (auto-reload is not enabled):
    .venv/bin/python main.py
    # or: .venv/bin/uvicorn main:app --reload
Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.

Run the Phase 1 print-statement demo instead of the server:
    .venv/bin/python main.py --demo

Libraries used: fastapi (web framework), uvicorn (the server that runs the
app), pydantic (via backend.transaction), plus the standard library
(datetime, sys, uuid).
"""

import sys
from datetime import date
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status

# format_cents lives in backend/transaction.py so plaid_services.py can share it.
from backend.transaction import Transaction, TransactionCreate, format_cents
from backend.transaction_manager import TransactionManager

# ---------------------------------------------------------------------------
# App and shared state
# ---------------------------------------------------------------------------

# title/description appear at the top of the auto-generated docs page (/docs).
app = FastAPI(
    title="Finance Tracker API",
    description="Basic transaction endpoints for the Finance Tracker backend.",
)

# One manager for the whole running server, so every request sees the same
# transactions. It lives at module level because there is no database yet.
_manager = TransactionManager()


def get_manager() -> TransactionManager:
    """Give an endpoint the TransactionManager to use.

    Endpoints receive the manager via ``Depends(get_manager)`` instead of
    touching ``_manager`` directly. This is FastAPI's "dependency injection":
    tests can swap in a fresh, empty manager with
    ``app.dependency_overrides[get_manager] = ...``, and later phases can
    replace this function with one that returns a database-backed manager
    without changing any endpoint code.
    """
    return _manager


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
# Notes that apply to every endpoint below:
#   * response_model tells FastAPI the shape of the response, so it can
#     serialize the Transaction to JSON (UUID -> string, date -> "YYYY-MM-DD")
#     and document it.
#   * A path parameter typed as UUID is validated automatically: a request to
#     /transactions/not-a-uuid gets a 422 before our code even runs.
#   * TransactionManager raises KeyError for a missing ID; we turn that into
#     HTTPException(404), which FastAPI sends as {"detail": "..."}.


@app.get(
    "/transactions",
    response_model=list[Transaction],
    operation_id="getAllTransaction",
    summary="List all transactions",
)
def get_all_transactions(
    manager: TransactionManager = Depends(get_manager),
) -> list[Transaction]:
    """Return every stored transaction, in the order they were added.

    Returns an empty list (not an error) when there are no transactions.
    """
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
    """Return the transaction with the given UUID, or 404 if it doesn't exist."""
    try:
        return manager.get_transaction(transaction_id)
    except KeyError:
        # "from None" hides the original KeyError from the traceback; the 404
        # is the expected result, not a server bug.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found",
        ) from None


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
    """Create a transaction from the request body and store it.

    The body is a TransactionCreate (amount, description, category, date), so
    the client cannot choose the ID. The server generates a new UUID and
    returns the full stored Transaction, including that ID, so the client
    knows how to fetch or delete it later.

    Example request body:
        {"amount": -450, "description": "Coffee",
         "category": "Food", "date": "2026-10-21"}
    """
    # By the time this runs, FastAPI has already validated the body against
    # TransactionCreate (and returned 422 if it was invalid).
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
    """Delete the transaction with the given UUID and return what was removed.

    Returns 404 if no transaction has that ID (including one already deleted).
    """
    try:
        return manager.delete_transaction(transaction_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found",
        ) from None


# ---------------------------------------------------------------------------
# Phase 1 demo (print statements, no server)
# ---------------------------------------------------------------------------


def print_transaction(txn: Transaction) -> None:
    """Print one transaction's fields on a single line."""
    print(
        f"  {txn.id} | {txn.date} | {txn.category:<7} | "
        f"{txn.description:<10} | {format_cents(txn.amount)}"
    )


def run_demo() -> None:
    """Run the Phase 1 demo steps and print the results of each.

    What it does, in order:
        1. Instantiates a TransactionManager.
        2. Creates and adds three transactions (one income, two expenses).
        3. Retrieves one transaction by its UUID.
        4. Calculates the net change across all transactions.
        5. Deletes one transaction and shows the updated state.

    It uses its own TransactionManager, separate from the API's, so running
    the demo never touches server data.
    """
    # --- Step 1: create the manager --------------------------------------
    manager = TransactionManager()
    print("Step 1: Created an empty TransactionManager")
    print(f"  Stored transactions: {len(manager)}\n")

    # --- Step 2: add three transactions ----------------------------------
    # Amounts are signed integer cents: positive = money in, negative = money out.
    paycheck = Transaction(
        amount=250000,  # +$2,500.00
        description="Paycheck",
        category="Salary",
        date=date(2026, 10, 15),
    )
    rent = Transaction(
        amount=-120000,  # -$1,200.00
        description="Rent",
        category="Housing",
        date=date(2026, 10, 1),
    )
    coffee = Transaction(
        amount=-450,  # -$4.50
        description="Coffee",
        category="Food",
        date=date(2026, 10, 21),
    )

    # add_transaction returns each transaction's UUID; keep them for lookups.
    paycheck_id = manager.add_transaction(paycheck)
    rent_id = manager.add_transaction(rent)
    coffee_id = manager.add_transaction(coffee)

    print("Step 2: Added three transactions")
    for txn_id in (paycheck_id, rent_id, coffee_id):
        print_transaction(manager.get_transaction(txn_id))
    print(f"  Stored transactions: {len(manager)}\n")

    # --- Step 3: retrieve one by UUID ------------------------------------
    found = manager.get_transaction(rent_id)
    print(f"Step 3: Retrieved transaction by UUID {rent_id}")
    print_transaction(found)
    # 'is' checks identity: the manager hands back the exact object we stored.
    print(f"  Same object that was added? {found is rent}\n")

    # --- Step 4: net change across all transactions ----------------------
    # Expected: 250000 - 120000 - 450 = 129550 cents ($1,295.50).
    net = manager.calculate_net_change()
    print("Step 4: Calculated net change")
    print(f"  Net change: {net} cents ({format_cents(net)})\n")

    # --- Step 5: delete one transaction ----------------------------------
    removed = manager.delete_transaction(coffee_id)
    print("Step 5: Deleted a transaction")
    print_transaction(removed)
    print(f"  Stored transactions: {len(manager)}")

    # The deleted ID should no longer be found; get_transaction raises KeyError.
    try:
        manager.get_transaction(coffee_id)
        print("  ERROR: deleted transaction is still retrievable")
    except KeyError as error:
        print(f"  Lookup after delete raises KeyError: {error}")

    # Net change should now exclude the coffee: 250000 - 120000 = 130000.
    net_after = manager.calculate_net_change()
    print(f"  Net change after delete: {net_after} cents ({format_cents(net_after)})")


# Only run when this file is executed directly (python main.py), not when it
# is imported by uvicorn ("uvicorn main:app") or by the tests.
if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    else:
        # Imported here, not at the top, so importing main (e.g. in tests)
        # doesn't require loading the server.
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8000)
