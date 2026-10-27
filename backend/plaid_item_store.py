"""Saves linked Plaid items in MySQL, with the access token encrypted. See docs/phase5.md."""

import os

from cryptography.fernet import Fernet, InvalidToken
from pymysql.connections import Connection

from backend.config import ConfigError, load_env_file
from backend.plaid_services import PlaidItem

KEY_HELP = (
    'generate one with: .venv/bin/python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


def load_token_key() -> str:
    """Read PLAID_TOKEN_KEY from .env / the environment.

    Raises:
        ConfigError: If it isn't set.
    """
    load_env_file()
    key = os.environ.get("PLAID_TOKEN_KEY")
    if not key:
        raise ConfigError(f"PLAID_TOKEN_KEY is not set in .env; {KEY_HELP}")
    return key


def make_fernet(key: str | bytes) -> Fernet:
    """Build the encryptor from a key.

    Raises:
        ConfigError: If the key isn't a valid Fernet key.
    """
    try:
        return Fernet(key)
    except (TypeError, ValueError):
        raise ConfigError(f"PLAID_TOKEN_KEY is not a valid key; {KEY_HELP}") from None


def encrypt_token(fernet: Fernet, token: str) -> str:
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(fernet: Fernet, encrypted: str) -> str:
    """Decrypt a stored token.

    Raises:
        ConfigError: If it can't be decrypted (usually PLAID_TOKEN_KEY changed).
    """
    try:
        return fernet.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ConfigError(
            "Could not decrypt a stored Plaid access token; was PLAID_TOKEN_KEY changed?"
        ) from None


class PlaidItemStore:
    """The ``plaid_items`` table: one row per linked bank, plus its sync cursor."""

    def __init__(self, conn: Connection, key: str | bytes) -> None:
        self._conn = conn
        self._fernet = make_fernet(key)

    def _one(self, sql: str, params: tuple) -> dict | None:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        self._conn.commit()
        return row

    def _write(self, sql: str, params: tuple) -> int:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params)
                count = cursor.rowcount
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        return count

    def save_item(self, item: PlaidItem, institution_id: str) -> None:
        """Insert or replace an item, storing its access token encrypted."""
        self._write(
            "INSERT INTO plaid_items (item_id, institution_id, access_token_encrypted) "
            "VALUES (%s, %s, %s) AS new "
            "ON DUPLICATE KEY UPDATE institution_id = new.institution_id, "
            "access_token_encrypted = new.access_token_encrypted",
            (item.item_id, institution_id, encrypt_token(self._fernet, item.access_token)),
        )

    def get_item(self, item_id: str) -> PlaidItem:
        """Load an item with its decrypted access token.

        Raises:
            KeyError: If the item isn't stored.
        """
        row = self._one(
            "SELECT item_id, access_token_encrypted FROM plaid_items WHERE item_id = %s",
            (item_id,),
        )
        if row is None:
            raise KeyError(f"Plaid item {item_id} not found")
        return PlaidItem(row["item_id"], decrypt_token(self._fernet, row["access_token_encrypted"]))

    def latest_item_id(self) -> str | None:
        """The most recently linked item, or None if none are stored."""
        row = self._one(
            "SELECT item_id FROM plaid_items ORDER BY created_at DESC, item_id LIMIT 1", ()
        )
        return row["item_id"] if row else None

    def get_cursor(self, item_id: str) -> str | None:
        """The saved /transactions/sync cursor, or None to start from the beginning."""
        row = self._one("SELECT sync_cursor FROM plaid_items WHERE item_id = %s", (item_id,))
        if row is None:
            raise KeyError(f"Plaid item {item_id} not found")
        return row["sync_cursor"] or None

    def save_cursor(self, item_id: str, cursor: str) -> None:
        """Save where the next sync should resume.

        Raises:
            KeyError: If the item isn't stored.
        """
        if not self._one("SELECT 1 AS found FROM plaid_items WHERE item_id = %s", (item_id,)):
            raise KeyError(f"Plaid item {item_id} not found")
        self._write(
            "UPDATE plaid_items SET sync_cursor = %s WHERE item_id = %s", (cursor, item_id)
        )
