"""Plaid Sandbox integration: connect a fake bank, then pull its transactions.

What this module does
---------------------
1. ``create_sandbox_public_token``  -> POST /sandbox/public_token/create
   Simulates a user linking a bank account. In production this step happens
   in the browser through Plaid Link; Sandbox lets us skip the UI and get a
   ``public_token`` directly for a fake institution and fake test user.
2. ``exchange_public_token``        -> POST /item/public_token/exchange
   Trades the short-lived ``public_token`` for a long-lived ``access_token``
   (plus an ``item_id``). The access token is what authorizes reading the
   account's data, so it is treated as a secret (see "Security" below).
3. ``sync_transactions``            -> POST /transactions/sync
   Fetches transactions page by page. Each response includes ``has_more``
   (are there more pages?) and ``next_cursor`` (where the next page starts).
   We keep calling until ``has_more`` is false. Saving the final cursor lets a
   later call fetch only what changed since.
4. ``to_transaction`` / ``format_summary``
   Converts Plaid transactions into our own ``Transaction`` model and prints
   a sanitized summary (no tokens, no full account IDs) for local testing.

Run the whole flow from the project root with:
    .venv/bin/python -m backend.plaid_services

Configuration
-------------
Credentials come from environment variables, which can be put in a ``.env``
file at the project root (git ignores it, so it is never committed):

    PLAID_CLIENT_ID=...
    PLAID_SECRET=...        # the *Sandbox* secret from the Plaid dashboard
    PLAID_ENV=sandbox

Only ``sandbox`` is accepted. Any other PLAID_ENV is refused, so this code
can't touch real bank accounts by accident.

Security
--------
* The client ID and secret are sent in the ``PLAID-CLIENT-ID`` /
  ``PLAID-SECRET`` HTTP headers, never in request bodies, and are never
  printed. ``PlaidClient`` and ``PlaidItem`` override their repr so printing
  or logging one doesn't reveal the secret or the access token.
* Every failure is raised as ``PlaidError``. Its message is built only from
  Plaid's documented error fields (error_type, error_code, error_message,
  request_id) or the network exception's class name. Nothing from our request
  (headers or body) goes into it.
* This module is separate from the FastAPI routes in main.py, and no endpoint
  returns an access token.

Plaid's amount sign convention
------------------------------
Plaid uses POSITIVE for money LEAVING the account (purchases) and NEGATIVE
for money ENTERING it (deposits, refunds). Our Transaction model is the
opposite (positive = income). ``to_transaction`` flips the sign and converts
dollars to integer cents.

Docs followed: https://plaid.com/docs/api/sandbox/,
https://plaid.com/docs/api/items/, https://plaid.com/docs/api/products/transactions/

Libraries used: httpx (HTTP client, already a project dependency), plus the
standard library (dataclasses, datetime, decimal, json, os, pathlib, re, sys, time).
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

# Base URL for each Plaid environment. Only Sandbox is allowed for now.
PLAID_BASE_URLS = {"sandbox": "https://sandbox.plaid.com"}

# The Plaid API version these request/response shapes follow. Plaid's
# official client libraries pin this same version.
PLAID_API_VERSION = "2020-09-14"

# Sandbox defaults, from https://plaid.com/docs/sandbox/test-credentials/
# - ins_109508 is "First Platypus Bank", Plaid's recommended non-OAuth test bank.
# - user_transactions_dynamic has realistic checking, credit card and loan
#   transaction history (any non-blank password works).
DEFAULT_SANDBOX_INSTITUTION = "ins_109508"
DEFAULT_SANDBOX_USERNAME = "user_transactions_dynamic"
DEFAULT_SANDBOX_PASSWORD = "pass_good"

# Plaid returns this when data changed while we were paging. The docs say the
# whole pagination loop must then restart from the first page's cursor.
MUTATION_DURING_PAGINATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"

# A valid environment variable name: letters, digits and underscores, not
# starting with a digit. Used to skip malformed .env lines.
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The only currency we convert. Our Transaction stores amounts as cents, which
# is only right for 2-decimal currencies, and the app shows everything in $.
SUPPORTED_CURRENCY = "USD"

# Project root (the folder above backend/), where the .env file lives.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PlaidError(Exception):
    """A Plaid API call failed. Safe to print: contains no credentials or tokens.

    Attributes:
        error_type: Plaid's broad category, e.g. "INVALID_INPUT", or "NETWORK"
            / "CONFIG" for errors raised by this module before reaching Plaid.
        error_code: Plaid's specific code, e.g. "INVALID_API_KEYS".
        error_message: Plaid's developer-facing explanation.
        request_id: Plaid's ID for the failed request (give this to Plaid
            support when troubleshooting). None if the request never got a reply.
        status_code: HTTP status code, or None if there was no response.
    """

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


# ---------------------------------------------------------------------------
# Data returned by the client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaidItem:
    """A linked (Sandbox) bank connection.

    ``repr=False`` on access_token keeps it out of the auto-generated repr,
    so ``print(item)`` shows only the item_id.
    """

    item_id: str
    access_token: str = field(repr=False)


@dataclass
class SyncResult:
    """Everything one full /transactions/sync run returned, across all pages.

    Attributes:
        added: New transactions (raw Plaid dicts).
        modified: Transactions that changed since the starting cursor.
        removed: Transactions that were deleted, as {"transaction_id", "account_id"}.
        next_cursor: Pass this to the next sync to get only newer changes.
        pages: How many /transactions/sync requests it took.
        update_status: Plaid's transactions_update_status from the last page,
            e.g. "HISTORICAL_UPDATE_COMPLETE" (all history pulled) or
            "INITIAL_UPDATE_COMPLETE" (only the last ~30 days so far).
    """

    added: list[dict[str, Any]] = field(default_factory=list)
    modified: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str = ""
    pages: int = 0
    update_status: str = ""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    A tiny stand-in for the python-dotenv library, so we don't need another
    dependency. Rules:
      * Blank lines and lines starting with '#' are skipped.
      * A leading "export " is ignored: export KEY=abc -> KEY=abc
        (lets the same file be used with the shell's "source .env").
      * A value starting with a quote is taken literally up to the matching
        closing quote: KEY="abc # 1" -> abc # 1, and KEY="abc"  # note -> abc.
        Without a closing quote, the value is used as-is.
      * Lines whose name isn't a valid variable name (e.g. "=abc") are skipped.
      * In an unquoted value, " #" starts a comment: KEY=abc  # note -> abc.
        A '#' with no space before it is kept, so KEY=abc#1 -> abc#1.
      * Variables that are already set are NOT overwritten, so a real
        environment variable always wins over the file.
      * A missing file is fine; the values may come from the environment.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # partition splits on the FIRST '=', so values may contain '='.
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        # Skip lines whose name isn't a valid variable name (e.g. "=abc").
        # os.environ would raise OSError on an empty name and crash startup.
        if not ENV_NAME_PATTERN.fullmatch(key):
            continue
        os.environ.setdefault(key, _parse_env_value(value.strip()))


def _parse_env_value(value: str) -> str:
    """Turn the raw text after '=' into the actual value (see load_env_file)."""
    # Quoted: keep everything up to the matching closing quote, comments
    # included, and ignore anything after it (e.g. KEY="abc"  # note -> abc).
    if value[:1] in ("'", '"'):
        closing = value.find(value[0], 1)
        if closing != -1:
            return value[1:closing]
        # No closing quote: fall through and treat the value as unquoted.
    # Unquoted: cut off a trailing " # comment", then any leftover spaces.
    for separator in (" #", "\t#"):
        if separator in value:
            value = value.split(separator, 1)[0]
    return value.strip()


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class PlaidClient:
    """Calls the Plaid API using httpx.

    Use it as a context manager so the HTTP connection is closed when you're done:

        with PlaidClient.from_env() as plaid:
            public_token = plaid.create_sandbox_public_token()
            item = plaid.exchange_public_token(public_token)
            result = plaid.sync_transactions(item.access_token)
    """

    def __init__(
        self,
        client_id: str,
        secret: str,
        env: str = "sandbox",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Create a client.

        Args:
            client_id: Plaid client ID.
            secret: Plaid secret for the chosen environment.
            env: Plaid environment name. Only "sandbox" is accepted.
            transport: Optional httpx transport. Tests pass an
                httpx.MockTransport here to fake Plaid's responses offline.
            timeout: Seconds to wait for each request before giving up.

        Raises:
            PlaidError: If credentials are missing, the secret is just the
                client ID pasted twice, or env isn't "sandbox".
        """
        if not client_id or not secret:
            raise PlaidError(
                "CONFIG", "MISSING_CREDENTIALS",
                "PLAID_CLIENT_ID and PLAID_SECRET must be set (see .env)",
            )
        # An easy copy-paste mistake. Plaid would only answer with a generic
        # INVALID_API_KEYS, so catch it here with a clearer message.
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
        # The credentials live only in these default headers. httpx sends them
        # with every request, so they never appear in the JSON bodies.
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
        """Build a client from PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV.

        Loads the project's .env file first (without overriding variables
        that are already set). Extra keyword args go to __init__.
        """
        load_env_file()
        return cls(
            client_id=os.environ.get("PLAID_CLIENT_ID", ""),
            secret=os.environ.get("PLAID_SECRET", ""),
            env=os.environ.get("PLAID_ENV", "sandbox"),
            **kwargs,
        )

    def __repr__(self) -> str:
        # Never include the credentials, even by accident in a log line.
        return f"PlaidClient(env={self.env!r})"

    # Context-manager support: "with PlaidClient(...) as plaid:" closes the
    # underlying HTTP connection pool automatically at the end of the block.
    def __enter__(self) -> "PlaidClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()

    # -- low-level request ---------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON body to a Plaid endpoint and return the parsed reply.

        Plaid sends money amounts as JSON numbers like 12.34. The default JSON
        parser would make those floats, which can't hold most decimal values
        exactly, so we parse them as Decimal instead.

        Raises:
            PlaidError: On network failures, non-200 replies, or a reply that
                isn't a JSON object. ``from None`` drops the original
                exception from the traceback, since it holds the request
                object (and so the request headers).
        """
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            # Use only the exception's class name (e.g. "ConnectTimeout"),
            # never str(exc) or the request.
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

        # Every Plaid reply (success or error) is a JSON object. Anything else,
        # e.g. a list from a misbehaving proxy, would break data.get() below.
        if not isinstance(data, dict):
            raise PlaidError(
                "API_ERROR", "INVALID_RESPONSE",
                f"Plaid returned unexpected JSON from {path}",
                status_code=response.status_code,
            )

        if response.status_code != 200:
            # Plaid's error object: https://plaid.com/docs/errors/
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
        """Check that a successful reply has the fields we're about to read.

        Without this, a missing field would raise a bare KeyError, which
        callers don't expect (they catch PlaidError) and whose traceback
        would show local variables such as the access token.

        Raises:
            PlaidError: Naming the missing fields (never their values).
        """
        missing = [name for name in fields if name not in data]
        if missing:
            raise PlaidError(
                "API_ERROR", "INVALID_RESPONSE",
                f"Plaid reply from {path} is missing {', '.join(missing)}",
                request_id=data.get("request_id"),
            )

    # -- Sandbox linking -------------------------------------------------------

    def create_sandbox_public_token(
        self,
        institution_id: str = DEFAULT_SANDBOX_INSTITUTION,
        username: str = DEFAULT_SANDBOX_USERNAME,
        password: str = DEFAULT_SANDBOX_PASSWORD,
    ) -> str:
        """Simulate linking a bank account and return a public_token.

        Calls POST /sandbox/public_token/create with Transactions enabled. This
        endpoint exists only in Sandbox; real accounts go through Plaid Link.

        Args:
            institution_id: The Sandbox test bank to link.
            username: Sandbox test user (controls what fake data you get).
            password: Sandbox test password.

        Returns:
            A short-lived public_token for exchange_public_token().
        """
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
        """Trade a public_token for a long-lived access_token.

        Calls POST /item/public_token/exchange.

        Returns:
            A PlaidItem holding the item_id and the access_token. Keep the
            access token private: never log it, print it, or return it from
            an API endpoint.
        """
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        self._require(data, "/item/public_token/exchange", "item_id", "access_token")
        return PlaidItem(item_id=data["item_id"], access_token=data["access_token"])

    # -- Transactions ----------------------------------------------------------

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
        """Fetch all transaction changes since ``cursor`` via /transactions/sync.

        Pagination (from Plaid's docs):
            1. Send the cursor (omit it for the first-ever sync).
            2. Collect that page's added / modified / removed lists.
            3. If has_more is true, send next_cursor and repeat.
            4. When has_more is false, next_cursor is the one to save for next time.
            If Plaid reports TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION
            (data changed mid-loop), throw away the partial results and
            restart from the ORIGINAL cursor.

        Data readiness:
            A freshly linked Item may not have data yet. Plaid then returns
            empty lists and an empty next_cursor (with a status of
            "NOT_READY", or briefly "INITIAL_UPDATE_COMPLETE"). On a
            first sync we poll every ``poll_interval_seconds`` until data is
            ready, or until the status is "HISTORICAL_UPDATE_COMPLETE"
            (Plaid is done, so an empty reply means no transactions). If ``wait_for_data_seconds`` runs out first we raise
            instead of returning an empty result, so the caller can't
            mistake "not ready" for "no transactions" (or save that cursor).

        Args:
            access_token: From exchange_public_token(). Used only in the
                request body; never logged.
            cursor: Where to resume. None = from the beginning.
            count: Transactions per page (Plaid allows 1-500).
            wait_for_data_seconds: Max time to wait for a NOT_READY Item.
            poll_interval_seconds: Delay between readiness checks.
            max_restarts: How many times to restart after a mid-pagination
                mutation before giving up.
            sleep: Function used to wait. Tests replace it so they run instantly.

        Returns:
            A SyncResult with every page's results combined.

        Raises:
            PlaidError: If any request fails, the restart limit is reached,
                or the data is still NOT_READY after wait_for_data_seconds.
        """
        restarts = 0
        waited = 0.0
        while True:
            result = SyncResult()
            page_cursor = cursor
            try:
                while True:
                    body: dict[str, Any] = {"access_token": access_token, "count": count}
                    # Leave the cursor out entirely on the first-ever sync.
                    if page_cursor:
                        body["cursor"] = page_cursor
                    page = self._post("/transactions/sync", body)
                    self._require(
                        page, "/transactions/sync",
                        "added", "modified", "removed", "next_cursor", "has_more",
                    )
                    # The three lists must really be lists; a null would make
                    # .extend() below raise a TypeError instead of a PlaidError.
                    for name in ("added", "modified", "removed"):
                        if not isinstance(page[name], list):
                            raise PlaidError(
                                "API_ERROR", "INVALID_RESPONSE",
                                f"Plaid reply from /transactions/sync has a non-list {name}",
                                request_id=page.get("request_id"),
                            )
                    result.pages += 1
                    result.update_status = page.get("transactions_update_status", "")

                    # Not ready yet: wait and ask again (first sync only).
                    # Plaid signals "no data yet" with an EMPTY next_cursor.
                    # The status alone isn't enough: tested against Sandbox, a
                    # new Item briefly reports INITIAL_UPDATE_COMPLETE while
                    # still returning no data and an empty cursor.
                    # HISTORICAL_UPDATE_COMPLETE means Plaid has finished, so
                    # an empty reply then is a real "no transactions" answer.
                    if (
                        not page_cursor
                        and not page["next_cursor"]
                        and not page["added"]
                        and result.update_status != "HISTORICAL_UPDATE_COMPLETE"
                    ):
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
                # Restart the entire loop from the original cursor, as the
                # docs require. The outer while builds a fresh SyncResult.
                restarts += 1


# ---------------------------------------------------------------------------
# Converting and displaying
# ---------------------------------------------------------------------------


def dollars_to_cents(amount: Decimal) -> int:
    """Convert a Decimal dollar amount to integer cents, rounding to the nearest cent.

    Only call this for USD (to_transaction checks the currency first), since
    "cents" only makes sense for 2-decimal currencies.

    Real bank amounts are whole cents, but Plaid Sandbox's dynamic test user
    returns some USD amounts with up to 7 decimal places (e.g. 9.7853775).
    Rejecting those dropped about 1 in 10 Sandbox transactions, so we round.

    Rounding uses ROUND_HALF_EVEN ("banker's rounding"): an exact half cent
    goes to the even neighbour (1.005 -> 100, 1.015 -> 102). Rounding every
    half up would bias totals upward over many transactions; half-even
    averages out, which is why it's the usual choice for money.

    Raises:
        ValueError: If the amount isn't a finite number (NaN or infinity).
    """
    try:
        # quantize(Decimal("1")) rounds to a whole number of cents.
        cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        raise ValueError(f"Amount {amount} is not a finite number") from None
    return int(cents)


def to_transaction(plaid_txn: dict[str, Any]) -> Transaction:
    """Convert one Plaid transaction dict into our Transaction model.

    Field mapping:
        amount      <- -(Plaid amount) in cents. The sign is flipped because
                       Plaid uses positive = money out; we use positive = money in.
        description <- merchant_name, falling back to name (merchant_name is
                       often null, e.g. for transfers).
        category    <- personal_finance_category.primary (e.g. "FOOD_AND_DRINK"),
                       or "UNCATEGORIZED" if Plaid didn't categorize it.
        date        <- date (the posted date, "YYYY-MM-DD").

    Only USD transactions are converted. Plaid gives the currency in
    iso_currency_code, or in unofficial_currency_code (with
    iso_currency_code null) for things like crypto. Anything else is
    refused, so a ¥1500 purchase can't turn into $1,500.00 or be added to
    a dollar total. A transaction with neither code is treated as USD.

    A new UUID is generated for the Transaction. Plaid's own transaction_id
    isn't stored yet; Phase 4 adds it as ``external_id`` so repeated syncs
    can be matched up instead of duplicated.

    Raises:
        ValueError: If the currency isn't USD, the amount is not a finite
            number, or the date is bad.
        KeyError: If a required field (amount, name, date) is missing.
        pydantic.ValidationError: If a field has the wrong type.
    """
    currency = plaid_txn.get("iso_currency_code") or plaid_txn.get("unofficial_currency_code")
    if currency and currency != SUPPORTED_CURRENCY:
        raise ValueError(f"Unsupported currency {currency}; only {SUPPORTED_CURRENCY} is converted")

    # personal_finance_category should be an object; anything else (null, or
    # an unexpected string) just means "no category".
    finance_category = plaid_txn.get("personal_finance_category")
    category = finance_category.get("primary") if isinstance(finance_category, dict) else None
    return Transaction(
        amount=-dollars_to_cents(Decimal(plaid_txn["amount"])),
        description=plaid_txn.get("merchant_name") or plaid_txn["name"],
        category=category or "UNCATEGORIZED",
        date=Date.fromisoformat(plaid_txn["date"]),
    )


def mask_id(value: str) -> str:
    """Show only the last 4 characters of an identifier: 'abc123xyz9' -> '…xyz9'."""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def format_summary(result: SyncResult, item: PlaidItem, limit: int = 15) -> str:
    """Build a human-readable, sanitized summary of a sync for local testing.

    Sanitized means: no access token, no cursor, and account / item IDs
    masked to their last 4 characters. Only data already shown in a normal
    banking app (date, merchant, category, amount) is printed in full.

    Pending transactions (Plaid's ``pending: true``) are marked with "P" and
    left out of the net change. When one posts, Plaid removes the pending
    version and adds a posted one with a NEW transaction_id, so counting
    pending amounts would count the same purchase twice later on.

    A transaction that can't be converted (e.g. a non-USD currency, a
    non-numeric amount or a missing field) is skipped and counted, rather
    than crashing the summary.

    Args:
        result: What sync_transactions() returned.
        item: The linked item (only its masked item_id is shown).
        limit: Max transactions to list individually (newest first).
    """
    transactions = []
    skipped = 0
    for txn in result.added:
        try:
            transactions.append((txn, to_transaction(txn)))
        except (AttributeError, KeyError, TypeError, ValueError):
            # pydantic.ValidationError is a subclass of ValueError, and so is
            # the "unsupported currency" error. AttributeError covers a row
            # that isn't a dict at all.
            skipped += 1
    # Newest first. ours.date is a real date, so this works even if one
    # transaction's raw "date" text was oddly formatted.
    transactions.sort(key=lambda pair: pair[1].date, reverse=True)
    posted = [ours for plaid_txn, ours in transactions if not plaid_txn.get("pending")]
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
            flag = "P" if plaid_txn.get("pending") else " "
            lines.append(
                f"    {flag} {ours.date} | acct {mask_id(plaid_txn.get('account_id') or '')} | "
                f"{ours.category[:22]:<22} | {ours.description[:28]:<28} | "
                f"{format_cents(ours.amount):>12}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the full Sandbox flow and print a sanitized summary.

    Returns:
        A process exit code: 0 on success, 1 if Plaid (or the network) failed.
    """
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
        # PlaidError's text is built only from Plaid's error fields, so it is
        # safe to print. stderr keeps errors separate from normal output.
        print(f"Plaid request failed: {error}", file=sys.stderr)
        return 1

    print(format_summary(result, item))
    return 0


# "python -m backend.plaid_services" runs main(); importing the module doesn't.
if __name__ == "__main__":
    sys.exit(main())
