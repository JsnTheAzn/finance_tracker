"""Offline tests for the Phase 5 pieces: database config, token encryption and sync_item."""

import os
import unittest
from unittest import mock

import httpx
import pymysql
from cryptography.fernet import Fernet

from backend import db
from backend.config import ConfigError
from backend.plaid_item_store import decrypt_token, encrypt_token, load_token_key, make_fernet
from backend.plaid_services import PlaidClient, PlaidItem
from backend.sync_service import sync_item
from backend.transaction_manager import SyncCounts, TransactionManager
from tests.test_plaid_services import ACCESS_TOKEN, CLIENT_ID, SECRET, FakePlaid, plaid_txn, sync_page

MYSQL_SETTINGS = {
    "MYSQL_HOST": "example.aivencloud.com",
    "MYSQL_PORT": "12345",
    "MYSQL_USER": "avnadmin",
    "MYSQL_PASSWORD": "db-password-should-never-leak",
    "MYSQL_DATABASE": "defaultdb",
    "MYSQL_SSL_CA": "certs/does-not-exist.pem",
}


class DbConfigTests(unittest.TestCase):
    def test_missing_settings_are_named(self):
        settings = {**MYSQL_SETTINGS, "MYSQL_PASSWORD": "", "MYSQL_PORT": ""}
        with mock.patch.dict(os.environ, settings), mock.patch("backend.db.load_env_file"):
            with self.assertRaises(ConfigError) as ctx:
                db.connect()
        self.assertIn("MYSQL_PASSWORD", str(ctx.exception))
        self.assertIn("MYSQL_PORT", str(ctx.exception))

    def test_missing_ca_file_is_reported(self):
        with mock.patch.dict(os.environ, MYSQL_SETTINGS), mock.patch("backend.db.load_env_file"):
            with self.assertRaises(ConfigError) as ctx:
                db.connect()
        self.assertIn("MYSQL_SSL_CA", str(ctx.exception))

    def test_connection_error_hides_password(self):
        with mock.patch.dict(os.environ, {**MYSQL_SETTINGS, "MYSQL_SSL_CA": __file__}), \
                mock.patch("backend.db.load_env_file"), \
                mock.patch("backend.db.pymysql.connect",
                           side_effect=pymysql.OperationalError(1045, "Access denied (using password: YES)")):
            with self.assertRaises(db.DatabaseError) as ctx:
                db.connect()
        message = str(ctx.exception)
        self.assertIn("1045", message)
        self.assertIn("wrong MYSQL_USER or MYSQL_PASSWORD", message)
        self.assertNotIn("db-password-should-never-leak", message)
        self.assertIsNone(ctx.exception.__cause__)

    def test_connect_uses_ssl_with_ca(self):
        with mock.patch.dict(os.environ, {**MYSQL_SETTINGS, "MYSQL_SSL_CA": __file__}), \
                mock.patch("backend.db.load_env_file"), \
                mock.patch("backend.db.pymysql.connect") as connect:
            db.connect(database="finance_test")
        kwargs = connect.call_args.kwargs
        self.assertEqual(kwargs["ssl"], {"ca": __file__})
        self.assertEqual(kwargs["database"], "finance_test")
        self.assertEqual(kwargs["port"], 12345)
        self.assertFalse(kwargs["autocommit"])

    def test_is_configured_follows_mysql_host(self):
        with mock.patch("backend.db.load_env_file"):
            with mock.patch.dict(os.environ, {"MYSQL_HOST": ""}):
                self.assertFalse(db.is_configured())
            with mock.patch.dict(os.environ, {"MYSQL_HOST": "h"}):
                self.assertTrue(db.is_configured())

    def test_schema_statements_split_cleanly(self):
        executed = []

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql):
                executed.append(sql)

        conn = mock.Mock()
        conn.cursor.return_value = FakeCursor()
        db.init_schema(conn)
        self.assertEqual(len(executed), 2)
        self.assertTrue(executed[0].startswith("CREATE TABLE IF NOT EXISTS transactions"))
        self.assertTrue(executed[1].startswith("CREATE TABLE IF NOT EXISTS plaid_items"))
        conn.commit.assert_called_once()


class TokenEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.fernet = make_fernet(Fernet.generate_key())

    def test_round_trip_and_no_plain_text(self):
        encrypted = encrypt_token(self.fernet, ACCESS_TOKEN)
        self.assertNotIn(ACCESS_TOKEN, encrypted)
        self.assertEqual(decrypt_token(self.fernet, encrypted), ACCESS_TOKEN)

    def test_wrong_key_gives_config_error(self):
        encrypted = encrypt_token(self.fernet, ACCESS_TOKEN)
        other = make_fernet(Fernet.generate_key())
        with self.assertRaises(ConfigError):
            decrypt_token(other, encrypted)

    def test_invalid_key_gives_config_error(self):
        with self.assertRaises(ConfigError):
            make_fernet("not-a-valid-key")

    def test_missing_key_gives_config_error(self):
        with mock.patch("backend.plaid_item_store.load_env_file"), \
                mock.patch.dict(os.environ, {"PLAID_TOKEN_KEY": ""}):
            with self.assertRaises(ConfigError) as ctx:
                load_token_key()
        self.assertIn("generate one", str(ctx.exception))


class FakeItemStore:
    """In-memory stand-in for PlaidItemStore."""

    def __init__(self):
        self.cursors: dict[str, str | None] = {"item-1": None}

    def get_item(self, item_id):
        return PlaidItem(item_id, ACCESS_TOKEN)

    def get_cursor(self, item_id):
        return self.cursors[item_id]

    def save_cursor(self, item_id, cursor):
        self.cursors[item_id] = cursor


class SyncItemTests(unittest.TestCase):
    def test_first_sync_then_resume_from_saved_cursor(self):
        fake = FakePlaid([
            sync_page([plaid_txn("t1", "4.50"), plaid_txn("t2", "-100.00")], next_cursor="c1"),
            sync_page([plaid_txn("t3", "1.00")], next_cursor="c2"),
        ])
        store = FakeItemStore()
        manager = TransactionManager()
        with PlaidClient(CLIENT_ID, SECRET, transport=httpx.MockTransport(fake.handle)) as plaid:
            first = sync_item(plaid, store, manager, "item-1")
            self.assertEqual(store.cursors["item-1"], "c1")
            second = sync_item(plaid, store, manager, "item-1")

        self.assertEqual(first.counts, SyncCounts(added=2, updated=0, removed=0))
        self.assertEqual(second.counts, SyncCounts(added=1, updated=0, removed=0))
        self.assertEqual(store.cursors["item-1"], "c2")
        sent = [body.get("cursor") for body in fake.bodies("/transactions/sync")]
        self.assertEqual(sent, [None, "c1"])  # second sync resumed from the saved cursor
        self.assertEqual(len(manager), 3)

    def test_cursor_not_saved_when_apply_fails(self):
        fake = FakePlaid([sync_page([plaid_txn("t1", "4.50")], next_cursor="c1")])
        store = FakeItemStore()
        manager = mock.Mock()
        manager.apply_sync.side_effect = RuntimeError("database went away")
        with PlaidClient(CLIENT_ID, SECRET, transport=httpx.MockTransport(fake.handle)) as plaid:
            with self.assertRaises(RuntimeError):
                sync_item(plaid, store, manager, "item-1")
        self.assertIsNone(store.cursors["item-1"])  # next run starts from the old cursor


if __name__ == "__main__":
    unittest.main()
