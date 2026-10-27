"""TransactionManager backed by MySQL, with the same methods and rules. See docs/phase5.md."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import Cursor

from backend.transaction import Transaction
from backend.transaction_manager import SyncCounts, validate_sync_input

COLUMNS = "id, amount, description, category, date, external_id, pending"


def _placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


def _row_to_transaction(row: dict[str, Any]) -> Transaction:
    return Transaction(
        id=UUID(row["id"]),
        amount=int(row["amount"]),
        description=row["description"],
        category=row["category"],
        date=row["date"],
        external_id=row["external_id"],
        pending=bool(row["pending"]),
    )


def _values(txn: Transaction) -> tuple:
    """Column values for COLUMNS, in order."""
    return (
        str(txn.id), txn.amount, txn.description, txn.category,
        txn.date, txn.external_id, txn.pending,
    )


class MySQLTransactionManager:
    """Stores Transactions in the MySQL ``transactions`` table. Every write commits or rolls back."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    @contextmanager
    def _transaction(self) -> Iterator[Cursor]:
        """Run the block in one DB transaction: commit on success, roll back on any error."""
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        self._conn.commit()  # ends the read so the next query sees fresh data
        return list(rows)

    def add_transaction(self, transaction: Transaction) -> UUID:
        """Store a transaction and return its ID.

        Raises:
            TypeError: If the argument is not a Transaction.
            ValueError: If the same ID, or the same external_id, is already stored.
        """
        if not isinstance(transaction, Transaction):
            raise TypeError("add_transaction expects a Transaction")
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    f"INSERT INTO transactions ({COLUMNS}) VALUES ({_placeholders(7)})",
                    _values(transaction),
                )
        except pymysql.IntegrityError:
            raise ValueError(f"Transaction {transaction.id} already exists") from None
        return transaction.id

    def get_transaction(self, transaction_id: UUID) -> Transaction:
        """Return the transaction with the given ID.

        Raises:
            KeyError: If no transaction has that ID.
        """
        rows = self._query(
            f"SELECT {COLUMNS} FROM transactions WHERE id = %s", (str(transaction_id),)
        )
        if not rows:
            raise KeyError(f"Transaction {transaction_id} not found")
        return _row_to_transaction(rows[0])

    def get_all_transactions(self) -> list[Transaction]:
        """Return every stored transaction, in the order they were added."""
        rows = self._query(f"SELECT {COLUMNS} FROM transactions ORDER BY seq")
        return [_row_to_transaction(row) for row in rows]

    def delete_transaction(self, transaction_id: UUID) -> Transaction:
        """Remove the transaction with the given ID and return it.

        Raises:
            KeyError: If no transaction has that ID.
        """
        with self._transaction() as cursor:
            cursor.execute(
                f"SELECT {COLUMNS} FROM transactions WHERE id = %s FOR UPDATE",
                (str(transaction_id),),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Transaction {transaction_id} not found")
            cursor.execute("DELETE FROM transactions WHERE id = %s", (str(transaction_id),))
        return _row_to_transaction(row)

    def calculate_net_change(self, include_pending: bool = True) -> int:
        """Return the sum of all amounts in cents, optionally leaving out pending ones."""
        sql = "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions"
        if not include_pending:
            sql += " WHERE pending = FALSE"
        return int(self._query(sql)[0]["total"])

    def apply_sync(
        self,
        added: list[Transaction],
        modified: list[Transaction],
        removed: list[str],
        replaces: dict[str, str] | None = None,
    ) -> SyncCounts:
        """Apply one bank sync's changes in a single DB transaction.

        Same rules as TransactionManager.apply_sync; nothing is saved if anything fails.

        Raises:
            TypeError: If an item in added/modified is not a Transaction.
            ValueError: If an item has no external_id, or its new UUID is
                already used by a different stored transaction.
        """
        replaces = replaces or {}
        incoming = [*added, *modified]
        validate_sync_input(incoming)

        wanted = {txn.external_id for txn in incoming} | set(replaces.values()) | set(removed)
        with self._transaction() as cursor:
            by_external_id: dict[str, UUID] = {}
            if wanted:
                cursor.execute(
                    "SELECT id, external_id FROM transactions "
                    f"WHERE external_id IN ({_placeholders(len(wanted))}) FOR UPDATE",
                    tuple(wanted),
                )
                by_external_id = {row["external_id"]: UUID(row["id"]) for row in cursor.fetchall()}

            unmatched_ids = [
                str(txn.id) for txn in incoming
                if by_external_id.get(txn.external_id) is None
                and by_external_id.get(replaces.get(txn.external_id)) is None
            ]
            if unmatched_ids:
                cursor.execute(
                    f"SELECT id FROM transactions WHERE id IN ({_placeholders(len(unmatched_ids))})",
                    tuple(unmatched_ids),
                )
                clash = cursor.fetchone()
                if clash is not None:
                    raise ValueError(f"Transaction {clash['id']} is already used by a different transaction")

            added_count = updated_count = removed_count = 0
            for txn in incoming:
                existing_id = by_external_id.get(txn.external_id)
                if existing_id is None and txn.external_id in replaces:
                    existing_id = by_external_id.pop(replaces[txn.external_id], None)

                if existing_id is None:
                    cursor.execute(
                        f"INSERT INTO transactions ({COLUMNS}) VALUES ({_placeholders(7)})",
                        _values(txn),
                    )
                    by_external_id[txn.external_id] = txn.id
                    added_count += 1
                else:
                    cursor.execute(
                        "UPDATE transactions SET amount = %s, description = %s, category = %s, "
                        "date = %s, external_id = %s, pending = %s WHERE id = %s",
                        (txn.amount, txn.description, txn.category, txn.date,
                         txn.external_id, txn.pending, str(existing_id)),
                    )
                    by_external_id[txn.external_id] = existing_id
                    updated_count += 1

            for external_id in removed:
                existing_id = by_external_id.pop(external_id, None)
                if existing_id is not None:
                    cursor.execute("DELETE FROM transactions WHERE id = %s", (str(existing_id),))
                    removed_count += 1

        return SyncCounts(added_count, updated_count, removed_count)

    def __len__(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM transactions")[0]["n"])
