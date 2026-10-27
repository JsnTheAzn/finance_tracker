"""Tests for the FastAPI endpoints in main.py.

These send real HTTP requests to the app through FastAPI's TestClient, which
runs the app in-process (no server needs to be started).

Run from the project root with:
    .venv/bin/python -m unittest discover -s tests -v

Libraries used: fastapi (TestClient, which needs httpx installed), plus the
standard library (unittest, uuid).
"""

import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from backend.transaction_manager import TransactionManager
import main
from main import app, get_manager

# A valid request body for POST /transactions. Dates are ISO strings in JSON.
COFFEE = {
    "amount": -450,
    "description": "Coffee",
    "category": "Food",
    "date": "2026-10-21",
}


class GetManagerTests(unittest.TestCase):
    def test_uses_in_memory_manager_without_mysql(self):
        original = main.USE_MYSQL
        main.USE_MYSQL = False
        try:
            self.assertIsInstance(next(get_manager()), TransactionManager)
        finally:
            main.USE_MYSQL = original


class TransactionApiTests(unittest.TestCase):
    """Tests for getAllTransaction, getTransaction, addTransaction, deleteTransaction."""

    def setUp(self):
        # Give every test its own empty manager so tests can't affect each
        # other (or the real server's data). dependency_overrides makes every
        # endpoint that uses Depends(get_manager) receive this one instead.
        self.manager = TransactionManager()
        app.dependency_overrides[get_manager] = lambda: self.manager
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def add(self, **overrides) -> dict:
        """POST a transaction (COFFEE plus any overrides) and return the JSON body."""
        response = self.client.post("/transactions", json={**COFFEE, **overrides})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # --- addTransaction ----------------------------------------------------

    def test_add_returns_created_transaction_with_id(self):
        body = self.add()
        self.assertEqual(body["amount"], -450)
        self.assertEqual(body["description"], "Coffee")
        self.assertEqual(body["category"], "Food")
        self.assertEqual(body["date"], "2026-10-21")
        UUID(body["id"])  # raises ValueError if the id isn't a valid UUID
        self.assertEqual(len(self.manager), 1)

    def test_add_shows_bank_fields_with_manual_defaults(self):
        body = self.add()
        self.assertIsNone(body["external_id"])
        self.assertFalse(body["pending"])

    def test_add_ignores_client_supplied_bank_fields(self):
        # external_id and pending aren't on TransactionCreate, so a client
        # can't pretend a manual transaction came from the bank.
        body = self.add(external_id="fake-plaid-id", pending=True)
        self.assertIsNone(body["external_id"])
        self.assertFalse(body["pending"])

    def test_add_rejects_text_longer_than_db_columns(self):
        self.assertEqual(self.client.post("/transactions", json={**COFFEE, "category": "x" * 101}).status_code, 422)
        self.assertEqual(self.client.post("/transactions", json={**COFFEE, "description": "x" * 513}).status_code, 422)

    def test_add_ignores_client_supplied_id(self):
        # TransactionCreate has no id field, so a sent id is dropped and the
        # server generates its own.
        chosen_id = str(uuid4())
        body = self.add(id=chosen_id)
        self.assertNotEqual(body["id"], chosen_id)

    def test_add_rejects_invalid_bodies(self):
        bad_bodies = [
            {**COFFEE, "amount": 4.5},  # money must be integer cents
            {**COFFEE, "amount": "450"},  # strict mode: no string -> int
            {**COFFEE, "date": "10/21/2026"},  # not an ISO date
            {k: v for k, v in COFFEE.items() if k != "category"},  # missing field
        ]
        for body in bad_bodies:
            # subTest reports each failing body separately.
            with self.subTest(body=body):
                response = self.client.post("/transactions", json=body)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.manager), 0)  # nothing was stored

    # --- getTransaction ----------------------------------------------------

    def test_get_transaction_by_id(self):
        created = self.add()
        response = self.client.get(f"/transactions/{created['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)

    def test_get_missing_transaction_returns_404(self):
        response = self.client.get(f"/transactions/{uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"])

    def test_get_with_invalid_uuid_returns_422(self):
        response = self.client.get("/transactions/not-a-uuid")
        self.assertEqual(response.status_code, 422)

    # --- getAllTransaction -------------------------------------------------

    def test_get_all_when_empty(self):
        response = self.client.get("/transactions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_all_returns_every_transaction(self):
        first = self.add(amount=250000, description="Paycheck", category="Salary")
        second = self.add()
        response = self.client.get("/transactions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [first, second])

    # --- deleteTransaction -------------------------------------------------

    def test_delete_transaction(self):
        created = self.add()
        response = self.client.delete(f"/transactions/{created['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)  # returns what was removed
        # It's gone: fetching or deleting it again is a 404.
        self.assertEqual(self.client.get(f"/transactions/{created['id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/transactions/{created['id']}").status_code, 404)

    def test_delete_missing_transaction_returns_404(self):
        response = self.client.delete(f"/transactions/{uuid4()}")
        self.assertEqual(response.status_code, 404)

    # --- docs --------------------------------------------------------------

    def test_operation_ids_use_requested_action_names(self):
        # The camelCase names are what the docs and generated clients show.
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertEqual(paths["/transactions"]["get"]["operationId"], "getAllTransaction")
        self.assertEqual(paths["/transactions"]["post"]["operationId"], "addTransaction")
        item = paths["/transactions/{transaction_id}"]
        self.assertEqual(item["get"]["operationId"], "getTransaction")
        self.assertEqual(item["delete"]["operationId"], "deleteTransaction")


if __name__ == "__main__":
    unittest.main()
