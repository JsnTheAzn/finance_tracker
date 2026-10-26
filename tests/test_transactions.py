"""Unit tests for Transaction and TransactionManager.

Run from the project root with:
    .venv/bin/python -m unittest discover -s tests -v

Libraries used: pydantic (for ValidationError), plus the standard library
(unittest, datetime, uuid).
"""

import unittest
from datetime import date
from uuid import UUID, uuid4

from pydantic import ValidationError

from backend.transaction import Transaction, TransactionCreate
from backend.transaction_manager import SyncCounts, TransactionManager


def make_transaction(amount: int = -450, **overrides) -> Transaction:
    """Build a Transaction with sensible defaults so each test stays short."""
    fields = {
        "amount": amount,
        "description": "Coffee",
        "category": "Food",
        "date": date(2026, 10, 21),
    }
    fields.update(overrides)
    return Transaction(**fields)


class TransactionTests(unittest.TestCase):
    """Tests for the Transaction data model."""

    def test_attributes_are_accessible_after_creation(self):
        txn = make_transaction()
        self.assertEqual(txn.amount, -450)
        self.assertEqual(txn.description, "Coffee")
        self.assertEqual(txn.category, "Food")
        self.assertEqual(txn.date, date(2026, 10, 21))
        self.assertIsInstance(txn.id, UUID)

    def test_each_transaction_gets_a_unique_id(self):
        self.assertNotEqual(make_transaction().id, make_transaction().id)

    def test_existing_id_can_be_supplied(self):
        known_id = uuid4()
        self.assertEqual(make_transaction(id=known_id).id, known_id)

    def test_amount_must_be_an_integer(self):
        with self.assertRaises(ValidationError):
            make_transaction(amount=4.50)  # floats are not allowed for money
        with self.assertRaises(ValidationError):
            make_transaction(amount=True)  # bool is technically an int; reject it
        with self.assertRaises(ValidationError):
            make_transaction(amount="450")  # strict mode: no string -> int coercion

    def test_iso_date_string_is_converted_to_date(self):
        # FastAPI hands Pydantic the date as a string, so ISO strings must work.
        self.assertEqual(make_transaction(date="2026-10-21").date, date(2026, 10, 21))

    def test_date_must_be_a_date_or_iso_string(self):
        with self.assertRaises(ValidationError):
            make_transaction(date="10/21/2026")  # not ISO format
        with self.assertRaises(ValidationError):
            make_transaction(date="2026-02-30")  # not a real calendar date
        with self.assertRaises(ValidationError):
            make_transaction(date=20261021)  # strict mode: no int -> date

    def test_missing_field_raises(self):
        with self.assertRaises(ValidationError):
            Transaction(amount=100, description="No category", date=date(2026, 10, 21))

    def test_json_input_parses_date_and_id_strings(self):
        # This is what FastAPI will receive: dates and UUIDs arrive as strings.
        known_id = uuid4()
        txn = Transaction.model_validate_json(
            '{"amount": -450, "description": "Coffee", "category": "Food",'
            f' "date": "2026-10-21", "id": "{known_id}"}}'
        )
        self.assertEqual(txn.date, date(2026, 10, 21))
        self.assertEqual(txn.id, known_id)


class TransactionCreateTests(unittest.TestCase):
    """Tests for the client-facing TransactionCreate model."""

    def test_has_no_id_field(self):
        data = TransactionCreate(
            amount=-450, description="Coffee", category="Food", date=date(2026, 10, 21)
        )
        self.assertFalse(hasattr(data, "id"))

    def test_from_create_copies_fields_and_assigns_id(self):
        data = TransactionCreate(
            amount=-450, description="Coffee", category="Food", date=date(2026, 10, 21)
        )
        txn = Transaction.from_create(data)
        self.assertEqual(txn.amount, data.amount)
        self.assertEqual(txn.description, data.description)
        self.assertEqual(txn.category, data.category)
        self.assertEqual(txn.date, data.date)
        self.assertIsInstance(txn.id, UUID)


class TransactionManagerTests(unittest.TestCase):
    """Tests for TransactionManager operations."""

    def setUp(self):
        # setUp runs before every test, so each test gets a fresh, empty manager.
        self.manager = TransactionManager()

    def test_add_and_get_transaction(self):
        txn = make_transaction()
        txn_id = self.manager.add_transaction(txn)
        self.assertEqual(txn_id, txn.id)
        self.assertIs(self.manager.get_transaction(txn_id), txn)
        self.assertEqual(len(self.manager), 1)

    def test_adding_duplicate_id_raises(self):
        txn = make_transaction()
        self.manager.add_transaction(txn)
        with self.assertRaises(ValueError):
            self.manager.add_transaction(txn)

    def test_add_rejects_non_transaction(self):
        with self.assertRaises(TypeError):
            self.manager.add_transaction({"amount": 100})

    def test_get_missing_transaction_raises(self):
        with self.assertRaises(KeyError):
            self.manager.get_transaction(uuid4())

    def test_get_all_transactions(self):
        self.assertEqual(self.manager.get_all_transactions(), [])
        first, second = make_transaction(100), make_transaction(-200)
        self.manager.add_transaction(first)
        self.manager.add_transaction(second)
        # Returned in insertion order.
        self.assertEqual(self.manager.get_all_transactions(), [first, second])

    def test_get_all_returns_a_copy(self):
        self.manager.add_transaction(make_transaction())
        self.manager.get_all_transactions().clear()  # mutate the returned list
        self.assertEqual(len(self.manager), 1)  # manager is unaffected

    def test_delete_transaction(self):
        txn = make_transaction()
        self.manager.add_transaction(txn)
        removed = self.manager.delete_transaction(txn.id)
        self.assertIs(removed, txn)
        self.assertEqual(len(self.manager), 0)
        with self.assertRaises(KeyError):
            self.manager.get_transaction(txn.id)

    def test_delete_missing_transaction_raises(self):
        with self.assertRaises(KeyError):
            self.manager.delete_transaction(uuid4())

    def test_net_change_of_empty_manager_is_zero(self):
        self.assertEqual(self.manager.calculate_net_change(), 0)

    def test_net_change_sums_income_and_expenses(self):
        self.manager.add_transaction(make_transaction(250000, category="Salary"))
        self.manager.add_transaction(make_transaction(-450))
        self.manager.add_transaction(make_transaction(-120000, category="Rent"))
        self.assertEqual(self.manager.calculate_net_change(), 129550)

    def test_net_change_updates_after_delete(self):
        keep = make_transaction(1000)
        drop = make_transaction(-300)
        self.manager.add_transaction(keep)
        self.manager.add_transaction(drop)
        self.manager.delete_transaction(drop.id)
        self.assertEqual(self.manager.calculate_net_change(), 1000)

    def test_net_change_can_leave_out_pending(self):
        self.manager.add_transaction(make_transaction(-450))
        self.manager.add_transaction(make_transaction(-2000, pending=True))
        self.assertEqual(self.manager.calculate_net_change(), -2450)  # default: all
        self.assertEqual(self.manager.calculate_net_change(include_pending=False), -450)


def bank_txn(external_id: str, amount: int = -450, **overrides) -> Transaction:
    """A Transaction as it would come from a bank sync (external_id set)."""
    return make_transaction(amount, external_id=external_id, **overrides)


class ApplySyncTests(unittest.TestCase):
    """Tests for TransactionManager.apply_sync (Phase 4).

    They only use public methods, so the same tests can later run against
    the database-backed manager.
    """

    def setUp(self):
        self.manager = TransactionManager()

    def test_new_fields_default_for_manual_transactions(self):
        txn = make_transaction()
        self.assertIsNone(txn.external_id)
        self.assertFalse(txn.pending)

    def test_applying_same_sync_twice_adds_nothing_new(self):
        added = [bank_txn("p1", -450), bank_txn("p2", 250000)]
        first = self.manager.apply_sync(added, [], [])
        ids_before = {t.external_id: t.id for t in self.manager.get_all_transactions()}

        # A later sync builds brand-new Transaction objects (new UUIDs) for
        # the same Plaid transactions, so simulate that here.
        again = [bank_txn("p1", -450), bank_txn("p2", 250000)]
        second = self.manager.apply_sync(again, [], [])

        self.assertEqual(first, SyncCounts(added=2, updated=0, removed=0))
        self.assertEqual(second, SyncCounts(added=0, updated=2, removed=0))
        self.assertEqual(len(self.manager), 2)
        ids_after = {t.external_id: t.id for t in self.manager.get_all_transactions()}
        self.assertEqual(ids_after, ids_before)  # our UUIDs didn't change

    def test_modified_updates_in_place_and_keeps_uuid(self):
        self.manager.apply_sync([bank_txn("p1", -2000, pending=True)], [], [])
        original_id = self.manager.get_all_transactions()[0].id

        counts = self.manager.apply_sync([], [bank_txn("p1", -2150, pending=False)], [])

        self.assertEqual(counts, SyncCounts(added=0, updated=1, removed=0))
        stored = self.manager.get_transaction(original_id)
        self.assertEqual(stored.amount, -2150)
        self.assertFalse(stored.pending)
        self.assertEqual(stored.id, original_id)

    def test_unknown_modified_is_inserted(self):
        counts = self.manager.apply_sync([], [bank_txn("never-seen")], [])
        self.assertEqual(counts, SyncCounts(added=1, updated=0, removed=0))
        self.assertEqual(len(self.manager), 1)

    def test_removed_deletes_and_unknown_ids_are_ignored(self):
        self.manager.apply_sync([bank_txn("p1"), bank_txn("p2")], [], [])
        counts = self.manager.apply_sync([], [], ["p1", "not-stored"])
        self.assertEqual(counts, SyncCounts(added=0, updated=0, removed=1))
        self.assertEqual([t.external_id for t in self.manager.get_all_transactions()], ["p2"])

    def test_pending_posting_flow(self):
        # Plaid: a pending charge posts -> pending one removed, posted one added.
        self.manager.apply_sync([bank_txn("pending-1", -2000, pending=True)], [], [])
        counts = self.manager.apply_sync([bank_txn("posted-1", -2000)], [], ["pending-1"])
        self.assertEqual(counts, SyncCounts(added=1, updated=0, removed=1))
        self.assertEqual(len(self.manager), 1)
        self.assertEqual(self.manager.calculate_net_change(), -2000)  # not counted twice

    def test_manual_transactions_are_never_touched(self):
        manual = make_transaction(-999)
        self.manager.add_transaction(manual)
        self.manager.apply_sync([bank_txn("p1")], [], ["p1"])
        self.assertIs(self.manager.get_transaction(manual.id), manual)
        self.assertEqual(len(self.manager), 1)

    def test_posted_replaces_pending_and_keeps_uuid(self):
        self.manager.apply_sync([bank_txn("pending-1", -2000, pending=True)], [], [])
        pending_uuid = self.manager.get_all_transactions()[0].id

        counts = self.manager.apply_sync(
            [bank_txn("posted-1", -2150)], [], ["pending-1"], replaces={"posted-1": "pending-1"}
        )

        self.assertEqual(counts, SyncCounts(added=0, updated=1, removed=0))
        stored = self.manager.get_transaction(pending_uuid)  # same UUID as before
        self.assertEqual(stored.external_id, "posted-1")
        self.assertEqual(stored.amount, -2150)
        self.assertFalse(stored.pending)
        self.assertEqual(len(self.manager), 1)
        # A later re-sync of the posted transaction matches by its new external_id.
        again = self.manager.apply_sync([bank_txn("posted-1", -2150)], [], [])
        self.assertEqual(again, SyncCounts(added=0, updated=1, removed=0))

    def test_replaces_with_unknown_pending_inserts_new(self):
        counts = self.manager.apply_sync([bank_txn("posted-1")], [], [], replaces={"posted-1": "gone"})
        self.assertEqual(counts, SyncCounts(added=1, updated=0, removed=0))

    def test_uuid_collision_with_other_transaction_is_rejected(self):
        manual = make_transaction(-999)
        self.manager.add_transaction(manual)
        clash = bank_txn("p1", id=manual.id)
        with self.assertRaises(ValueError):
            self.manager.apply_sync([bank_txn("p0"), clash], [], [])
        self.assertIs(self.manager.get_transaction(manual.id), manual)  # untouched
        self.assertEqual(len(self.manager), 1)  # p0 wasn't half-applied

    def test_stored_transactions_are_copies(self):
        txn = bank_txn("p1", -450)
        self.manager.apply_sync([txn], [], [])
        txn.amount = -999_999  # the caller changes its own object afterwards
        self.assertEqual(self.manager.get_transaction(txn.id).amount, -450)

    def test_non_transaction_input_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.manager.apply_sync([{"transaction_id": "raw-plaid-dict"}], [], [])

    def test_missing_external_id_is_rejected_before_any_change(self):
        with self.assertRaises(ValueError):
            self.manager.apply_sync([bank_txn("p1"), make_transaction()], [], [])
        self.assertEqual(len(self.manager), 0)  # nothing half-applied


if __name__ == "__main__":
    unittest.main()
