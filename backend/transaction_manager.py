"""Defines TransactionManager, which stores and operates on Transaction objects.

Data structure
--------------
Transactions are kept in a dictionary that maps each transaction's UUID to the
Transaction object:

    {
        UUID('3f2b...'): Transaction(amount=-450, ...),
        UUID('9a1c...'): Transaction(amount=250000, ...),
    }

Python dictionaries are hash tables. When a UUID is used as a key, Python calls
hash() on it and uses the result to decide where the entry lives. That is why
adding, looking up, and deleting by ID are all O(1) on average: no matter how
many transactions are stored, finding one by ID takes about the same time.

Libraries used: standard library only (uuid).
"""

from uuid import UUID

from backend.transaction import Transaction


class TransactionManager:
    """In-memory store for transactions, keyed by their UUID.

    Example:
        >>> manager = TransactionManager()
        >>> txn_id = manager.add_transaction(txn)
        >>> manager.get_transaction(txn_id) is txn
        True
        >>> manager.calculate_net_change()
        -450
    """

    def __init__(self) -> None:
        """Create an empty manager."""
        # The leading underscore marks this as internal. Other code should go
        # through the methods below instead of editing the dict directly, so
        # rules like "no duplicate IDs" are always enforced.
        self._transactions: dict[UUID, Transaction] = {}

    def add_transaction(self, transaction: Transaction) -> UUID:
        """Store a transaction and return its ID.

        Args:
            transaction: The Transaction to store.

        Returns:
            The UUID of the stored transaction, so the caller can look it up later.

        Raises:
            TypeError: If the argument is not a Transaction.
            ValueError: If a transaction with the same ID is already stored.
                Overwriting silently would lose data, so this is an error.
        """
        if not isinstance(transaction, Transaction):
            raise TypeError("add_transaction expects a Transaction")
        if transaction.id in self._transactions:
            raise ValueError(f"Transaction {transaction.id} already exists")

        # The UUID is hashed by the dict to pick the storage slot.
        self._transactions[transaction.id] = transaction
        return transaction.id

    def get_transaction(self, transaction_id: UUID) -> Transaction:
        """Return the transaction with the given ID.

        Args:
            transaction_id: The UUID of the transaction to find.

        Returns:
            The matching Transaction.

        Raises:
            KeyError: If no transaction has that ID. Raising (instead of
                returning None) lets the future API layer turn it into a
                clean "404 Not Found" response.
        """
        try:
            return self._transactions[transaction_id]
        except KeyError:
            raise KeyError(f"Transaction {transaction_id} not found") from None

    def get_all_transactions(self) -> list[Transaction]:
        """Return every stored transaction as a list.

        A new list is built each call, so callers can loop over or sort it
        without affecting the manager's internal dict. Python dicts keep
        insertion order, so the list is in the order transactions were added.

        Returns:
            A list of all Transactions (empty if none are stored).
        """
        return list(self._transactions.values())

    def delete_transaction(self, transaction_id: UUID) -> Transaction:
        """Remove the transaction with the given ID and return it.

        Returning the removed object lets the caller confirm or display
        what was deleted.

        Args:
            transaction_id: The UUID of the transaction to delete.

        Returns:
            The Transaction that was removed.

        Raises:
            KeyError: If no transaction has that ID.
        """
        try:
            # dict.pop removes the key and gives back its value in one step.
            return self._transactions.pop(transaction_id)
        except KeyError:
            raise KeyError(f"Transaction {transaction_id} not found") from None

    def calculate_net_change(self) -> int:
        """Return the sum of all transaction amounts, in cents.

        Because amounts are signed, adding them together gives the net change:
        income (positive) minus spending (negative). An empty manager returns 0.

        Example: +250000 (paycheck) + -450 (coffee) = 249550 cents ($2,495.50).

        Returns:
            The net change in cents as a signed integer.
        """
        return sum(txn.amount for txn in self._transactions.values())

    def __len__(self) -> int:
        """Return how many transactions are stored, so len(manager) works."""
        return len(self._transactions)
