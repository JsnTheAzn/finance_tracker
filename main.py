"""Demo script that exercises Transaction and TransactionManager end to end.

What it does, in order:
    1. Instantiates a TransactionManager.
    2. Creates and adds three transactions (one income, two expenses).
    3. Retrieves one transaction by its UUID.
    4. Calculates the net change across all transactions.
    5. Deletes one transaction and shows the updated state.

Each step prints its result so you can verify the behavior by eye.

Run from the project root with:
    python3 main.py

Libraries used: standard library only (datetime). The project's own
backend package supplies Transaction and TransactionManager.
"""

from datetime import date

from backend.transaction import Transaction
from backend.transaction_manager import TransactionManager


def format_cents(cents: int) -> str:
    """Turn a signed number of cents into a readable dollar string.

    Amounts are stored as integer cents (see backend/transaction.py), so this
    is only for display. Integer division and modulo keep it exact; no floats.

    Example:
        >>> format_cents(-120000)
        '-$1,200.00'
    """
    sign = "-" if cents < 0 else ""
    # divmod splits the absolute value into whole dollars and leftover cents.
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def print_transaction(txn: Transaction) -> None:
    """Print one transaction's fields on a single line."""
    print(
        f"  {txn.id} | {txn.date} | {txn.category:<7} | "
        f"{txn.description:<10} | {format_cents(txn.amount)}"
    )


def main() -> None:
    """Run the demo steps and print the results of each."""
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


# Only run the demo when this file is executed directly (python3 main.py),
# not when it is imported by another module or a test.
if __name__ == "__main__":
    main()
