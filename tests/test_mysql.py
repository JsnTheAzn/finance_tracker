"""Tests that run against a real MySQL database (Aiven).

Skipped unless .env sets the MYSQL_* settings and MYSQL_TEST_DATABASE, which
must be a separate database from MYSQL_DATABASE: every test empties its tables.
"""

import os
import unittest

from backend import db
from backend.config import load_env_file
from backend.mysql_transaction_manager import MySQLTransactionManager
from backend.plaid_item_store import PlaidItemStore, make_fernet
from backend.plaid_services import PlaidItem
from tests.test_transactions import ManagerContract, bank_txn

load_env_file()
TEST_DATABASE = os.environ.get("MYSQL_TEST_DATABASE")
SKIP_REASON = "set MYSQL_* and MYSQL_TEST_DATABASE in .env to run MySQL tests"
TEST_KEY = "Hh4cG0Sk1o3rPj3ZKUfnI1u6t5U4VtJ1y7S3Lw6nXq8="


class MySQLTestCase(unittest.TestCase):
    """Opens one connection to the test database and empties the tables before each test."""

    @classmethod
    def setUpClass(cls):
        if TEST_DATABASE == os.environ.get("MYSQL_DATABASE"):
            raise unittest.SkipTest("MYSQL_TEST_DATABASE must differ from MYSQL_DATABASE")
        cls.conn = db.connect(database=TEST_DATABASE)
        db.init_schema(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM transactions")
            cursor.execute("DELETE FROM plaid_items")
        self.conn.commit()
        super().setUp()


@unittest.skipUnless(db.is_configured() and TEST_DATABASE, SKIP_REASON)
class MySQLManagerTests(ManagerContract, MySQLTestCase):
    def make_manager(self):
        return MySQLTransactionManager(self.conn)

    def test_external_ids_are_case_sensitive(self):
        counts = self.manager.apply_sync([bank_txn("abcD"), bank_txn("abcd")], [], [])
        self.assertEqual(counts.added, 2)


@unittest.skipUnless(db.is_configured() and TEST_DATABASE, SKIP_REASON)
class PlaidItemStoreTests(MySQLTestCase):
    def setUp(self):
        super().setUp()
        self.store = PlaidItemStore(self.conn, TEST_KEY)

    def test_save_and_get_item_round_trip(self):
        self.store.save_item(PlaidItem("item-1", "access-sandbox-secret"), "ins_109508")
        self.assertEqual(self.store.get_item("item-1"), PlaidItem("item-1", "access-sandbox-secret"))
        self.assertEqual(self.store.latest_item_id(), "item-1")

    def test_token_is_not_stored_in_plain_text(self):
        self.store.save_item(PlaidItem("item-1", "access-sandbox-secret"), "ins_109508")
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT access_token_encrypted FROM plaid_items")
            stored = cursor.fetchone()["access_token_encrypted"]
        self.conn.commit()
        self.assertNotIn("access-sandbox-secret", stored)
        self.assertEqual(make_fernet(TEST_KEY).decrypt(stored.encode()).decode(), "access-sandbox-secret")

    def test_cursor_starts_empty_and_is_saved(self):
        self.store.save_item(PlaidItem("item-1", "token"), "ins_109508")
        self.assertIsNone(self.store.get_cursor("item-1"))
        self.store.save_cursor("item-1", "cursor-123")
        self.assertEqual(self.store.get_cursor("item-1"), "cursor-123")

    def test_unknown_item_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.get_item("missing")
        with self.assertRaises(KeyError):
            self.store.save_cursor("missing", "c")
        self.assertIsNone(self.store.latest_item_id())


if __name__ == "__main__":
    unittest.main()
