"""Sync a stored Plaid item into the database, resuming from its saved cursor. See docs/phase5.md.

    .venv/bin/python -m backend.plaid_services            # reuse the stored item
    .venv/bin/python -m backend.plaid_services --relink   # link a new Sandbox item first
"""

import sys
from typing import NamedTuple, Protocol

import pymysql

from backend import db
from backend.config import ConfigError
from backend.mysql_transaction_manager import MySQLTransactionManager
from backend.plaid_item_store import PlaidItemStore, load_token_key
from backend.plaid_services import (
    DEFAULT_SANDBOX_INSTITUTION,
    PlaidClient,
    PlaidError,
    PlaidItem,
    mask_id,
    to_sync_changes,
)
from backend.transaction import format_cents
from backend.transaction_manager import SyncCounts


class ItemStore(Protocol):
    """What sync_item needs from an item store (PlaidItemStore, or a fake in tests)."""

    def get_item(self, item_id: str) -> PlaidItem: ...
    def get_cursor(self, item_id: str) -> str | None: ...
    def save_cursor(self, item_id: str, cursor: str) -> None: ...


class ItemSyncResult(NamedTuple):
    counts: SyncCounts
    skipped: int
    update_status: str


def sync_item(plaid: PlaidClient, store: ItemStore, manager, item_id: str) -> ItemSyncResult:
    """Fetch changes since the item's saved cursor, apply them, then save the new cursor.

    The cursor is saved only after apply_sync succeeds. If anything fails in
    between, the next run re-fetches from the old cursor, which is harmless
    because apply_sync is idempotent.
    """
    item = store.get_item(item_id)
    result = plaid.sync_transactions(item.access_token, cursor=store.get_cursor(item_id))
    changes = to_sync_changes(result)
    counts = changes.apply_to(manager)
    store.save_cursor(item_id, result.next_cursor)
    return ItemSyncResult(counts, changes.skipped, result.update_status)


def main(relink: bool = False) -> int:
    """Link (if needed) and sync into MySQL, printing a summary. Returns a process exit code."""
    try:
        conn = db.connect()
    except (ConfigError, db.DatabaseError) as error:
        print(f"Database setup failed: {error}", file=sys.stderr)
        return 1

    try:
        db.init_schema(conn)
        store = PlaidItemStore(conn, load_token_key())
        manager = MySQLTransactionManager(conn)
        with PlaidClient.from_env() as plaid:
            item_id = None if relink else store.latest_item_id()
            if item_id is None:
                print(f"Linking a new Plaid {plaid.env} item (institution {DEFAULT_SANDBOX_INSTITUTION})...")
                item = plaid.exchange_public_token(plaid.create_sandbox_public_token())
                store.save_item(item, DEFAULT_SANDBOX_INSTITUTION)
                item_id = item.item_id
                print(f"  Saved item {mask_id(item_id)} (access token encrypted)")
            else:
                print(f"Reusing saved item {mask_id(item_id)}")

            print("  Syncing from the saved cursor...\n")
            sync = sync_item(plaid, store, manager, item_id)
    except (ConfigError, PlaidError) as error:
        print(f"Sync failed: {error}", file=sys.stderr)
        return 1
    except pymysql.MySQLError as error:
        print(f"Sync failed: database error ({db.describe_error(error)})", file=sys.stderr)
        return 1
    else:
        print("Synced into MySQL")
        print(f"  This sync:  {sync.counts}")
        print(f"  Status:     {sync.update_status or 'unknown'}")
        print(f"  Skipped:    {sync.skipped} (could not convert)")
        print(f"  Stored:     {len(manager)} transactions")
        print(f"  Net change, posted only: {format_cents(manager.calculate_net_change(include_pending=False))}")
        return 0
    finally:
        conn.close()
