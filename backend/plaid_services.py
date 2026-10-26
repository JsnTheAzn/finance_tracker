"""Plaid Sandbox client: link a fake bank, sync its transactions, load them into a manager.

Run from the project root with ``.venv/bin/python -m backend.plaid_services``.
Credentials come from a git-ignored .env file (see .env.example). Only the
Sandbox environment is allowed. See docs/phase3.md and docs/phase4.md.
"""

import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as Date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from backend.transaction import Transaction, format_cents
from backend.transaction_manager import SyncCounts, TransactionManager

PLAID_BASE_URLS = {"sandbox": "https://sandbox.plaid.com"}
PLAID_API_VERSION = "2020-09-14"

# Sandbox test bank and user, from https://plaid.com/docs/sandbox/test-credentials/
DEFAULT_SANDBOX_INSTITUTION = "ins_109508"
DEFAULT_SANDBOX_USERNAME = "user_transactions_dynamic"
DEFAULT_SANDBOX_PASSWORD = "pass_good"

MUTATION_DURING_PAGINATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SUPPORTED_CURRENCY = "USD"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PlaidError(Exception):
    """A Plaid call failed. Built only from Plaid's error fields, so it is safe to print."""

    def __init__(
        self,
        error_type: str,
        error_code: str,
        error_message: str,
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.error_type = error_type
        self.error_code = error_code
        self.error_message = error_message
        self.request_id = request_id
        self.status_code = status_code
        super().__init__(str(self))

    def __str__(self) -> str:
        text = f"{self.error_type}/{self.error_code}: {self.error_message}"
        if self.request_id:
            text += f" (request_id={self.request_id})"
        return text


@dataclass(frozen=True)
class PlaidItem:
    """A linked bank connection. The access token is left out of repr()."""

    item_id: str
    access_token: str = field(repr=False)


@dataclass
class SyncResult:
    """Everything one full /transactions/sync run returned, across all pages (raw Plaid dicts)."""

    added: list[dict[str, Any]] = field(default_factory=list)
    modified: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str = ""
    pages: int = 0
    update_status: str = ""


@dataclass
class SyncChanges:
    """A sync converted into the arguments TransactionManager.apply_sync takes."""

    added: list[Transaction] = field(default_factory=list)
    modified: list[Transaction] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    replaces: dict[str, str] = field(default_factory=dict)
    skipped: int = 0

    def apply_to(self, manager: TransactionManager) -> SyncCounts:
        """Apply these changes to a manager."""
        return manager.apply_sync(self.added, self.modified, self.removed, self.replaces)


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not ENV_NAME_PATTERN.fullmatch(key):
            continue
        os.environ.setdefault(key, _parse_env_value(value.strip()))


def _parse_env_value(value: str) -> str:
    """Handle quotes and trailing ' # comments' in a .env value."""
    if value[:1] in ("'", '"'):
        closing = value.find(value[0], 1)
        if closing != -1:
            return value[1:closing]
    for separator in (" #", "\t#"):
        if separator in value:
            value = value.split(separator, 1)[0]
    return value.strip()


class PlaidClient:
    """Calls the Plaid API with httpx. Use as a context manager to close the connection."""

    def __init__(
        self,
        client_id: str,
        secret: str,
        env: str = "sandbox",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Create a client. ``transport`` lets tests fake Plaid offline.

        Raises:
            PlaidError: If credentials are missing or identical, or env isn't "sandbox".
        """
        if not client_id or not secret:
            raise PlaidError(
                "CONFIG", "MISSING_CREDENTIALS",
                "PLAID_CLIENT_ID and PLAID_SECRET must be set (see .env)",
            )
        if client_id == secret:
            raise PlaidError(
                "CONFIG", "SECRET_MATCHES_CLIENT_ID",
                "PLAID_SECRET is the same as PLAID_CLIENT_ID; copy the Sandbox "
                "secret from https://dashboard.plaid.com/developers/keys",
            )
        if env not in PLAID_BASE_URLS:
            raise PlaidError(
                "CONFIG", "UNSUPPORTED_ENVIRONMENT",
                f"PLAID_ENV must be one of {sorted(PLAID_BASE_URLS)}; got {env!r}",
            )
        self.env = env
        # Credentials go only in headers, never in request bodies.
        self._http = httpx.Client(
            base_url=PLAID_BASE_URLS[env],
            headers={
                "PLAID-CLIENT-ID": client_id,
                "PLAID-SECRET": secret,
                "Plaid-Version": PLAID_API_VERSION,
            },
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls, **kwargs: Any) -> "PlaidClient":
        """Build a client from PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV (loads .env first)."""
        load_env_file()
        return cls(
            client_id=os.environ.get("PLAID_CLIENT_ID", ""),
            secret=os.environ.get("PLAID_SECRET", ""),
            env=os.environ.get("PLAID_ENV", "sandbox"),
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"PlaidClient(env={self.env!r})"

    def __enter__(self) -> "PlaidClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to Plaid and return the JSON reply, with amounts parsed as Decimal.

        Raises:
            PlaidError: On network failures, non-200 replies, or a reply that isn't a JSON object.
        """
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            # "from None" keeps the request (and its headers) out of the traceback.
            raise PlaidError(
                "NETWORK", type(exc).__name__, f"Could not reach Plaid at {path}"
            ) from None

        try:
            data = json.loads(response.text, parse_float=Decimal)
        except ValueError:
            raise PlaidError(
                "API_ERROR", "INVALID_RESPONSE",
                f"Plaid returned non-JSON data from {path}",
                status_code=response.status_code,
            ) from None

        if not isinstance(data, dict):
            raise PlaidError(
                "API_ERROR", "INVALID_RESPONSE",
                f"Plaid returned unexpected JSON from {path}",
                status_code=response.status_code,
            )

        if response.status_code != 200:
            raise PlaidError(
                error_type=data.get("error_type", "API_ERROR"),
                error_code=data.get("error_code", f"HTTP_{response.status_code}"),
                error_message=data.get("error_message", "Unknown Plaid error"),
                request_id=data.get("request_id"),
                status_code=response.status_code,
            )
        return data

    @staticmethod
    def _require(data: dict[str, Any], path: str, *fields: str) -> None:
        """Raise PlaidError (naming fields, never values) if a reply is missing fields."""
        missing = [name for name in fields if name not in data]
        if missing:
            raise PlaidError(
                "API_ERROR", "INVALID_RESPONSE",
                f"Plaid reply from {path} is missing {', '.join(missing)}",
                request_id=data.get("request_id"),
            )

    def create_sandbox_public_token(
        self,
        institution_id: str = DEFAULT_SANDBOX_INSTITUTION,
        username: str = DEFAULT_SANDBOX_USERNAME,
        password: str = DEFAULT_SANDBOX_PASSWORD,
    ) -> str:
        """Simulate a user linking a bank (Sandbox only) and return a public_token."""
        data = self._post(
            "/sandbox/public_token/create",
            {
                "institution_id": institution_id,
                "initial_products": ["transactions"],
                "options": {
                    "override_username": username,
                    "override_password": password,
                },
            },
        )
        self._require(data, "/sandbox/public_token/create", "public_token")
        return data["public_token"]

    def exchange_public_token(self, public_token: str) -> PlaidItem:
        """Trade a public_token for a long-lived access token. Never log or return the token."""
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        self._require(data, "/item/public_token/exchange", "item_id", "access_token")
        return PlaidItem(item_id=data["item_id"], access_token=data["access_token"])

    def sync_transactions(
        self,
        access_token: str,
        cursor: str | None = None,
        count: int = 100,
        wait_for_data_seconds: float = 30.0,
        poll_interval_seconds: float = 2.0,
        max_restarts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> SyncResult:
        """Fetch every change since ``cursor`` from /transactions/sync, following pagination.

        On a first sync, waits while Plaid isn't ready yet. Restarts from the
        original cursor if Plaid reports a mutation during pagination.

        Raises:
            PlaidError: If a request fails, restarts run out, or data is
                still not ready after ``wait_for_data_seconds``.
        """
        restarts = 0
        waited = 0.0
        while True:
            result = SyncResult()
            page_cursor = cursor
            try:
                while True:
                    body: dict[str, Any] = {"access_token": access_token, "count": count}
                    if page_cursor:
                        body["cursor"] = page_cursor
                    page = self._post("/transactions/sync", body)
                    self._require(
                        page, "/transactions/sync",
                        "added", "modified", "removed", "next_cursor", "has_more",
                    )
                    for name in ("added", "modified", "removed"):
                        if not isinstance(page[name], list):
                            raise PlaidError(
                                "API_ERROR", "INVALID_RESPONSE",
                                f"Plaid reply from /transactions/sync has a non-list {name}",
                                request_id=page.get("request_id"),
                            )
                    result.pages += 1
                    result.update_status = page.get("transactions_update_status", "")

                    # An empty cursor means "not ready"; the status can't be trusted for this.
                    if not page_cursor and not page["next_cursor"] and not page["added"]:
                        if waited >= wait_for_data_seconds:
                            raise PlaidError(
                                "TRANSACTIONS", "DATA_NOT_READY",
                                f"Transactions were still not ready after "
                                f"{wait_for_data_seconds:g}s; try syncing again later",
                                request_id=page.get("request_id"),
                            )
                        sleep(poll_interval_seconds)
                        waited += poll_interval_seconds
                        result.pages = 0
                        continue

                    result.added.extend(page["added"])
                    result.modified.extend(page["modified"])
                    result.removed.extend(page["removed"])
                    page_cursor = page["next_cursor"]
                    if not page["has_more"]:
                        result.next_cursor = page_cursor
                        return result
            except PlaidError as error:
                if error.error_code != MUTATION_DURING_PAGINATION or restarts >= max_restarts:
                    raise
                restarts += 1


def dollars_to_cents(amount: Decimal) -> int:
    """Convert USD to integer cents, rounding half-even (Sandbox sends sub-cent amounts).

    Raises:
        ValueError: If the amount isn't a finite number.
    """
    try:
        cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        raise ValueError(f"Amount {amount} is not a finite number") from None
    return int(cents)


def to_transaction(plaid_txn: dict[str, Any]) -> Transaction:
    """Convert one Plaid transaction into our Transaction. Plaid's sign is flipped.

    Raises:
        ValueError: Non-USD currency, non-finite amount, or bad date
            (pydantic's ValidationError is a ValueError too).
        KeyError: A required field is missing.
    """
    currency = plaid_txn.get("iso_currency_code") or plaid_txn.get("unofficial_currency_code")
    if currency and currency != SUPPORTED_CURRENCY:
        raise ValueError(f"Unsupported currency {currency}; only {SUPPORTED_CURRENCY} is converted")

    finance_category = plaid_txn.get("personal_finance_category")
    category = finance_category.get("primary") if isinstance(finance_category, dict) else None
    return Transaction(
        amount=-dollars_to_cents(Decimal(plaid_txn["amount"])),
        description=plaid_txn.get("merchant_name") or plaid_txn["name"],
        category=category or "UNCATEGORIZED",
        date=Date.fromisoformat(plaid_txn["date"]),
        external_id=plaid_txn["transaction_id"],
        pending=bool(plaid_txn.get("pending")),
    )


CONVERSION_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


def convert_transactions(
    plaid_txns: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], Transaction]], int]:
    """Convert Plaid transactions, skipping bad rows. Returns ([(plaid_dict, ours)], skipped)."""
    pairs = []
    skipped = 0
    for plaid_txn in plaid_txns:
        try:
            pairs.append((plaid_txn, to_transaction(plaid_txn)))
        except CONVERSION_ERRORS:
            skipped += 1
    return pairs, skipped


def to_sync_changes(result: SyncResult) -> SyncChanges:
    """Turn a Plaid sync into SyncChanges, the only place that knows Plaid's shapes."""
    added, skipped_added = convert_transactions(result.added)
    modified, skipped_modified = convert_transactions(result.modified)
    changes = SyncChanges(
        added=[ours for _, ours in added],
        modified=[ours for _, ours in modified],
        skipped=skipped_added + skipped_modified,
    )

    # A posted transaction names the pending one it replaces.
    for plaid_txn, ours in [*added, *modified]:
        pending_id = plaid_txn.get("pending_transaction_id")
        if pending_id:
            changes.replaces[ours.external_id] = pending_id

    for entry in result.removed:
        transaction_id = entry.get("transaction_id") if isinstance(entry, dict) else None
        if transaction_id:
            changes.removed.append(transaction_id)
        else:
            changes.skipped += 1
    return changes


def mask_id(value: str) -> str:
    """Show only the last 4 characters of an identifier: 'abc123xyz9' -> '…xyz9'."""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def format_summary(result: SyncResult, item: PlaidItem, limit: int = 15) -> str:
    """A sanitized summary of a sync: no tokens or cursors, IDs masked, pending flagged."""
    transactions, skipped = convert_transactions(result.added)
    transactions.sort(key=lambda pair: pair[1].date, reverse=True)
    posted = [ours for _, ours in transactions if not ours.pending]
    net = sum(ours.amount for ours in posted)

    lines = [
        "Plaid Sandbox transaction summary",
        f"  Item:      {mask_id(item.item_id)}",
        f"  Status:    {result.update_status or 'unknown'}",
        f"  Pages:     {result.pages}",
        f"  Added:     {len(result.added)} ({len(transactions) - len(posted)} pending)",
        f"  Modified:  {len(result.modified)}",
        f"  Removed:   {len(result.removed)}",
        f"  Accounts:  {len({txn.get('account_id') for txn, _ in transactions})}",
        f"  Net change of posted added (positive = money in): {format_cents(net)}",
    ]
    if skipped:
        lines.append(f"  Skipped:   {skipped} (could not convert)")
    if transactions:
        lines.append(f"  Date range: {transactions[-1][1].date} to {transactions[0][1].date}")
        lines.append(f"\n  Most recent {min(limit, len(transactions))} (P = pending):")
        for plaid_txn, ours in transactions[:limit]:
            flag = "P" if ours.pending else " "
            lines.append(
                f"    {flag} {ours.date} | acct {mask_id(plaid_txn.get('account_id') or '')} | "
                f"{ours.category[:22]:<22} | {ours.description[:28]:<28} | "
                f"{format_cents(ours.amount):>12}"
            )
    return "\n".join(lines)


def load_into_manager(result: SyncResult) -> str:
    """Load a sync into a new manager, then re-sync the same data to check nothing is duplicated."""
    manager = TransactionManager()
    first_changes = to_sync_changes(result)
    first = first_changes.apply_to(manager)
    stored_after_first = len(manager)

    # A fresh conversion gives new UUIDs, exactly like a real second sync.
    second = to_sync_changes(result).apply_to(manager)

    pending = sum(1 for txn in manager.get_all_transactions() if txn.pending)
    lines = [
        "Loaded into TransactionManager",
        f"  First sync:   {first}",
        f"  Second sync:  {second}  (same data again)",
        f"  Stored:       {len(manager)} ({pending} pending)",
        f"  Skipped:      {first_changes.skipped} (could not convert)",
        f"  Net change, posted only: {format_cents(manager.calculate_net_change(include_pending=False))}",
        f"  Net change, incl. pending: {format_cents(manager.calculate_net_change())}",
    ]
    if second.added == 0 and len(manager) == stored_after_first:
        lines.append("  OK: re-syncing the same data stored nothing new")
    else:
        lines.append("  WARNING: re-syncing the same data changed the stored count")
    return "\n".join(lines)


def main() -> int:
    """Run the full Sandbox flow and print the summaries. Returns a process exit code."""
    try:
        with PlaidClient.from_env() as plaid:
            print(f"Connecting to Plaid {plaid.env} (institution {DEFAULT_SANDBOX_INSTITUTION})...")
            public_token = plaid.create_sandbox_public_token()
            print("  Created Sandbox public token")
            item = plaid.exchange_public_token(public_token)
            print(f"  Exchanged for an access token (item {mask_id(item.item_id)})")
            print("  Syncing transactions...\n")
            result = plaid.sync_transactions(item.access_token)
    except PlaidError as error:
        print(f"Plaid request failed: {error}", file=sys.stderr)
        return 1

    print(format_summary(result, item))
    print()
    print(load_into_manager(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
