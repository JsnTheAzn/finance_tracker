"""In-memory store for transactions, keyed by UUID. See docs/phase1.md and docs/phase4.md."""

from typing import NamedTuple
from uuid import UUID

from backend.transaction import Transaction


class SyncCounts(NamedTuple):
    """How many transactions apply_sync added, updated and removed."""

    added: int
    updated: int
    removed: int


class TransactionManager:
    """Stores Transactions in a dict keyed by their UUID."""

    def __init__(self) -> None:
        self._transactions: dict[UUID, Transaction] = {}

    def add_transaction(self, transaction: Transaction) -> UUID:
        """Store a transaction and return its ID.

        Raises:
            TypeError: If the argument is not a Transaction.
            ValueError: If a transaction with the same ID is already stored.
        """
        if not isinstance(transaction, Transaction):
            raise TypeError("add_transaction expects a Transaction")
        if transaction.id in self._transactions:
            raise ValueError(f"Transaction {transaction.id} already exists")
        self._transactions[transaction.id] = transaction
        return transaction.id

    def get_transaction(self, transaction_id: UUID) -> Transaction:
        """Return the transaction with the given ID.

        Raises:
            KeyError: If no transaction has that ID.
        """
        try:
            return self._transactions[transaction_id]
        except KeyError:
            raise KeyError(f"Transaction {transaction_id} not found") from None

    def get_all_transactions(self) -> list[Transaction]:
        """Return every stored transaction, in the order they were added."""
        return list(self._transactions.values())

    def delete_transaction(self, transaction_id: UUID) -> Transaction:
        """Remove the transaction with the given ID and return it.

        Raises:
            KeyError: If no transaction has that ID.
        """
        try:
            return self._transactions.pop(transaction_id)
        except KeyError:
            raise KeyError(f"Transaction {transaction_id} not found") from None

    def calculate_net_change(self, include_pending: bool = True) -> int:
        """Return the sum of all amounts in cents, optionally leaving out pending ones."""
        return sum(
            txn.amount
            for txn in self._transactions.values()
            if include_pending or not txn.pending
        )

    def apply_sync(
        self,
        added: list[Transaction],
        modified: list[Transaction],
        removed: list[str],
        replaces: dict[str, str] | None = None,
    ) -> SyncCounts:
        """Apply one bank sync's changes, matching transactions by external_id.

        added and modified are upserts that keep an existing transaction's
        UUID; removed lists external_ids to delete (unknown ones are ignored).
        replaces maps a new external_id to the one it supersedes, e.g. a
        posted transaction replacing its pending version, so that record is
        updated in place instead of deleted and re-added. Transactions without
        an external_id are never touched. Nothing changes if validation fails.

        Raises:
            TypeError: If an item in added/modified is not a Transaction.
            ValueError: If an item has no external_id, or its new UUID is
                already used by a different stored transaction.
        """
        replaces = replaces or {}
        incoming = [*added, *modified]
        for txn in incoming:
            if not isinstance(txn, Transaction):
                raise TypeError("apply_sync expects Transactions in added and modified")
            if not txn.external_id:
                raise ValueError(f"Transaction {txn.id} has no external_id")

        by_external_id = {
            txn.external_id: txn.id
            for txn in self._transactions.values()
            if txn.external_id is not None
        }

        for txn in incoming:
            match = by_external_id.get(txn.external_id) or by_external_id.get(
                replaces.get(txn.external_id)
            )
            if match is None and txn.id in self._transactions:
                raise ValueError(
                    f"Transaction {txn.id} is already used by a different transaction"
                )

        added_count = updated_count = removed_count = 0
        for txn in incoming:
            existing_id = by_external_id.get(txn.external_id)
            if existing_id is None and txn.external_id in replaces:
                existing_id = by_external_id.pop(replaces[txn.external_id], None)

            if existing_id is None:
                self._transactions[txn.id] = txn.model_copy()
                by_external_id[txn.external_id] = txn.id
                added_count += 1
            else:
                self._transactions[existing_id] = txn.model_copy(update={"id": existing_id})
                by_external_id[txn.external_id] = existing_id
                updated_count += 1

        for external_id in removed:
            existing_id = by_external_id.pop(external_id, None)
            if existing_id is not None:
                del self._transactions[existing_id]
                removed_count += 1

        return SyncCounts(added_count, updated_count, removed_count)

    def __len__(self) -> int:
        return len(self._transactions)
