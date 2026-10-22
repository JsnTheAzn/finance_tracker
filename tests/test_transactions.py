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
from backend.transaction_manager import TransactionManager


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

    def test_date_must_be_a_date(self):
        # In Python code, strict mode requires a real datetime.date object.
        with self.assertRaises(ValidationError):
            make_transaction(date="2026-10-21")

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


if __name__ == "__main__":
    unittest.main()
