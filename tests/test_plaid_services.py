"""Offline tests for backend/plaid_services.py.

No network access or Plaid credentials are needed: httpx.MockTransport hands
each request to a Python function (``FakePlaid.handle``) that returns canned
responses shaped like Plaid's documented responses.

Run from the project root with:
    .venv/bin/python -m unittest discover -s tests -v

Libraries used: httpx (MockTransport), plus the standard library
(unittest, json, decimal, datetime, os, tempfile, pathlib).
"""

import json
import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

import httpx

from backend.plaid_services import (
    MUTATION_DURING_PAGINATION,
    PlaidClient,
    PlaidError,
    PlaidItem,
    SyncResult,
    dollars_to_cents,
    format_summary,
    load_env_file,
    to_transaction,
)

# Fake credentials. The tests check these never leak into errors or output.
CLIENT_ID = "test-client-id-123"
SECRET = "test-secret-should-never-leak"
ACCESS_TOKEN = "access-sandbox-should-never-leak"


def plaid_txn(txn_id: str, amount: str, **overrides) -> dict:
    """Build a Plaid-style transaction dict (amount as Decimal, like our parser)."""
    txn = {
        "transaction_id": txn_id,
        "account_id": "acct-checking-0001",
        "amount": Decimal(amount),
        "date": "2026-10-20",
        "name": "Raw Bank Name",
        "merchant_name": "Starbucks",
        "personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_COFFEE"},
    }
    txn.update(overrides)
    return txn


def to_json_txn(txn: dict) -> dict:
    """Turn a plaid_txn() dict into what Plaid would send as JSON (amount as a number)."""
    return {**txn, "amount": float(txn["amount"])}


class FakePlaid:
    """Pretends to be the Plaid API. Records each request for assertions.

    ``sync_pages`` is a list of responses (dict) or errors (tuple of
    status, body) returned in order for /transactions/sync calls.
    """

    def __init__(self, sync_pages=None):
        self.sync_pages = list(sync_pages or [])
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/sandbox/public_token/create":
            return httpx.Response(200, json={"public_token": "public-sandbox-abc", "request_id": "r1"})
        if path == "/item/public_token/exchange":
            return httpx.Response(
                200, json={"access_token": ACCESS_TOKEN, "item_id": "item-xyz-9876", "request_id": "r2"}
            )
        if path == "/transactions/sync":
            page = self.sync_pages.pop(0)
            if isinstance(page, tuple):  # (status, error body)
                return httpx.Response(page[0], json=page[1])
            return httpx.Response(200, json=page)
        return httpx.Response(404, json={"error_type": "INVALID_REQUEST", "error_code": "NOT_FOUND",
                                         "error_message": "unknown path"})

    def bodies(self, path: str) -> list[dict]:
        """The JSON bodies sent to one endpoint, in order."""
        return [json.loads(r.content) for r in self.requests if r.url.path == path]


def sync_page(added=(), has_more=False, next_cursor="cursor-end", status="HISTORICAL_UPDATE_COMPLETE"):
    """Build one /transactions/sync response."""
    return {
        "added": [to_json_txn(t) for t in added],
        "modified": [],
        "removed": [],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "transactions_update_status": status,
        "accounts": [],
        "request_id": "r-sync",
    }


MUTATION_ERROR = (400, {
    "error_type": "TRANSACTIONS_ERROR",
    "error_code": MUTATION_DURING_PAGINATION,
    "error_message": "underlying transaction data changed since last page was fetched",
    "request_id": "r-mut",
})


class PlaidClientTests(unittest.TestCase):
    def make_client(self, fake: FakePlaid) -> PlaidClient:
        return PlaidClient(CLIENT_ID, SECRET, transport=httpx.MockTransport(fake.handle))

    # --- configuration -----------------------------------------------------

    def test_rejects_non_sandbox_environment(self):
        with self.assertRaises(PlaidError) as ctx:
            PlaidClient(CLIENT_ID, SECRET, env="production")
        self.assertEqual(ctx.exception.error_code, "UNSUPPORTED_ENVIRONMENT")

    def test_rejects_missing_credentials(self):
        with self.assertRaises(PlaidError) as ctx:
            PlaidClient("", "")
        self.assertEqual(ctx.exception.error_code, "MISSING_CREDENTIALS")

    def test_rejects_secret_equal_to_client_id(self):
        with self.assertRaises(PlaidError) as ctx:
            PlaidClient(CLIENT_ID, CLIENT_ID)
        self.assertEqual(ctx.exception.error_code, "SECRET_MATCHES_CLIENT_ID")
        self.assertNotIn(CLIENT_ID, str(ctx.exception))  # value never echoed

    def test_repr_hides_secrets(self):
        client = PlaidClient(CLIENT_ID, SECRET)
        self.assertNotIn(SECRET, repr(client))
        self.assertNotIn(ACCESS_TOKEN, repr(PlaidItem("item-1", ACCESS_TOKEN)))

    # --- linking -----------------------------------------------------------

    def test_sandbox_link_and_exchange(self):
        fake = FakePlaid()
        with self.make_client(fake) as plaid:
            public_token = plaid.create_sandbox_public_token()
            item = plaid.exchange_public_token(public_token)

        self.assertEqual(public_token, "public-sandbox-abc")
        self.assertEqual(item, PlaidItem("item-xyz-9876", ACCESS_TOKEN))
        create_body = fake.bodies("/sandbox/public_token/create")[0]
        self.assertEqual(create_body["initial_products"], ["transactions"])
        self.assertEqual(fake.bodies("/item/public_token/exchange")[0], {"public_token": "public-sandbox-abc"})

    def test_credentials_go_in_headers_not_bodies(self):
        fake = FakePlaid()
        with self.make_client(fake) as plaid:
            plaid.create_sandbox_public_token()
        request = fake.requests[0]
        self.assertEqual(request.headers["PLAID-CLIENT-ID"], CLIENT_ID)
        self.assertEqual(request.headers["PLAID-SECRET"], SECRET)
        self.assertIn("Plaid-Version", request.headers)
        self.assertNotIn(SECRET, request.content.decode())

    # --- /transactions/sync pagination --------------------------------------

    def test_sync_follows_cursor_until_has_more_is_false(self):
        fake = FakePlaid([
            sync_page([plaid_txn("t1", "4.50")], has_more=True, next_cursor="c1"),
            sync_page([plaid_txn("t2", "12.00")], has_more=True, next_cursor="c2"),
            sync_page([plaid_txn("t3", "-1500.00")], has_more=False, next_cursor="c3"),
        ])
        with self.make_client(fake) as plaid:
            result = plaid.sync_transactions(ACCESS_TOKEN)

        self.assertEqual([t["transaction_id"] for t in result.added], ["t1", "t2", "t3"])
        self.assertEqual(result.next_cursor, "c3")
        self.assertEqual(result.pages, 3)
        cursors = [body.get("cursor") for body in fake.bodies("/transactions/sync")]
        self.assertEqual(cursors, [None, "c1", "c2"])  # first call omits the cursor

    def test_sync_resumes_from_given_cursor(self):
        fake = FakePlaid([sync_page(next_cursor="c9")])
        with self.make_client(fake) as plaid:
            plaid.sync_transactions(ACCESS_TOKEN, cursor="c5")
        self.assertEqual(fake.bodies("/transactions/sync")[0]["cursor"], "c5")

    def test_sync_restarts_from_original_cursor_after_mutation(self):
        fake = FakePlaid([
            sync_page([plaid_txn("stale", "1.00")], has_more=True, next_cursor="c1"),
            MUTATION_ERROR,
            sync_page([plaid_txn("t1", "1.00")], has_more=True, next_cursor="c1b"),
            sync_page([plaid_txn("t2", "2.00")], has_more=False, next_cursor="c2b"),
        ])
        with self.make_client(fake) as plaid:
            result = plaid.sync_transactions(ACCESS_TOKEN, cursor="start")

        # The partial page from before the error was discarded.
        self.assertEqual([t["transaction_id"] for t in result.added], ["t1", "t2"])
        cursors = [body["cursor"] for body in fake.bodies("/transactions/sync")]
        self.assertEqual(cursors, ["start", "c1", "start", "c1b"])

    def test_sync_gives_up_after_max_restarts(self):
        fake = FakePlaid([MUTATION_ERROR] * 3)
        with self.make_client(fake) as plaid:
            with self.assertRaises(PlaidError):
                plaid.sync_transactions(ACCESS_TOKEN, max_restarts=2)

    def test_sync_waits_while_data_not_ready(self):
        sleeps = []
        fake = FakePlaid([
            sync_page(next_cursor="", status="NOT_READY"),
            sync_page(next_cursor="", status="NOT_READY"),
            sync_page([plaid_txn("t1", "4.50")], next_cursor="c1"),
        ])
        with self.make_client(fake) as plaid:
            result = plaid.sync_transactions(ACCESS_TOKEN, sleep=sleeps.append)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.update_status, "HISTORICAL_UPDATE_COMPLETE")

    def test_sync_waits_while_cursor_empty_even_if_initial_update_complete(self):
        # Seen in real Sandbox: status flips to INITIAL_UPDATE_COMPLETE a moment
        # before data arrives, still with an empty cursor. Keep waiting.
        sleeps = []
        fake = FakePlaid([
            sync_page(next_cursor="", status="NOT_READY"),
            sync_page(next_cursor="", status="INITIAL_UPDATE_COMPLETE"),
            sync_page([plaid_txn("t1", "4.50")], next_cursor="c1"),
        ])
        with self.make_client(fake) as plaid:
            result = plaid.sync_transactions(ACCESS_TOKEN, sleep=sleeps.append)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.next_cursor, "c1")

    def test_sync_raises_when_data_never_becomes_ready(self):
        # Always NOT_READY: after the wait budget, raise instead of returning
        # an empty result that looks like "no transactions".
        fake = FakePlaid([sync_page(next_cursor="", status="NOT_READY")] * 4)
        sleeps = []
        with self.make_client(fake) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.sync_transactions(
                    ACCESS_TOKEN, wait_for_data_seconds=4, poll_interval_seconds=2, sleep=sleeps.append
                )
        self.assertEqual(ctx.exception.error_code, "DATA_NOT_READY")
        self.assertEqual(sleeps, [2, 2])  # waited 4s total, then gave up

    def test_sync_returns_empty_when_history_complete_with_no_data(self):
        # An Item that really has no transactions: Plaid is finished
        # (HISTORICAL_UPDATE_COMPLETE), so don't wait or raise.
        sleeps = []
        fake = FakePlaid([sync_page(next_cursor="", status="HISTORICAL_UPDATE_COMPLETE")])
        with self.make_client(fake) as plaid:
            result = plaid.sync_transactions(ACCESS_TOKEN, sleep=sleeps.append)
        self.assertEqual(sleeps, [])
        self.assertEqual(result.added, [])
        self.assertEqual(result.update_status, "HISTORICAL_UPDATE_COMPLETE")

    # --- error handling ----------------------------------------------------

    def test_api_error_is_raised_without_secrets(self):
        fake = FakePlaid([(400, {
            "error_type": "INVALID_INPUT",
            "error_code": "INVALID_ACCESS_TOKEN",
            "error_message": "provided access token is in an invalid format",
            "request_id": "r-bad",
        })])
        with self.make_client(fake) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.sync_transactions(ACCESS_TOKEN)

        error = ctx.exception
        self.assertEqual(error.error_code, "INVALID_ACCESS_TOKEN")
        self.assertEqual(error.request_id, "r-bad")
        self.assertEqual(error.status_code, 400)
        for secret in (SECRET, ACCESS_TOKEN):
            self.assertNotIn(secret, str(error))
        self.assertIsNone(error.__cause__)  # original exception not chained

    def test_network_error_is_raised_without_secrets(self):
        def fail(request):
            raise httpx.ConnectError("connection refused", request=request)

        with PlaidClient(CLIENT_ID, SECRET, transport=httpx.MockTransport(fail)) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.exchange_public_token("public-sandbox-abc")

        self.assertEqual(ctx.exception.error_type, "NETWORK")
        self.assertEqual(ctx.exception.error_code, "ConnectError")
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_non_json_response_raises_plaid_error(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(502, text="<html>Bad Gateway</html>"))
        with PlaidClient(CLIENT_ID, SECRET, transport=transport) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.create_sandbox_public_token()
        self.assertEqual(ctx.exception.error_code, "INVALID_RESPONSE")

    def test_success_reply_missing_fields_raises_plaid_error(self):
        # A 200 reply without access_token must be a PlaidError, not a KeyError.
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"request_id": "r-x"}))
        with PlaidClient(CLIENT_ID, SECRET, transport=transport) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.exchange_public_token("public-sandbox-abc")
        self.assertEqual(ctx.exception.error_code, "INVALID_RESPONSE")
        self.assertIn("access_token", str(ctx.exception))  # names the field only
        self.assertEqual(ctx.exception.request_id, "r-x")

    def test_sync_page_missing_fields_raises_plaid_error(self):
        page = sync_page()
        del page["has_more"]
        fake = FakePlaid([page])
        with self.make_client(fake) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.sync_transactions(ACCESS_TOKEN)
        self.assertEqual(ctx.exception.error_code, "INVALID_RESPONSE")

    def test_sync_page_with_null_list_raises_plaid_error(self):
        page = sync_page()
        page["modified"] = None
        fake = FakePlaid([page])
        with self.make_client(fake) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.sync_transactions(ACCESS_TOKEN)
        self.assertEqual(ctx.exception.error_code, "INVALID_RESPONSE")
        self.assertIn("modified", str(ctx.exception))

    def test_non_object_json_raises_plaid_error(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(400, json=["not", "an", "object"]))
        with PlaidClient(CLIENT_ID, SECRET, transport=transport) as plaid:
            with self.assertRaises(PlaidError) as ctx:
                plaid.create_sandbox_public_token()
        self.assertEqual(ctx.exception.error_code, "INVALID_RESPONSE")
        self.assertEqual(ctx.exception.status_code, 400)


class ConversionTests(unittest.TestCase):
    def test_dollars_to_cents_is_exact(self):
        self.assertEqual(dollars_to_cents(Decimal("0.10")), 10)
        self.assertEqual(dollars_to_cents(Decimal("1234.56")), 123456)
        self.assertEqual(dollars_to_cents(Decimal("-89.40")), -8940)

    def test_dollars_to_cents_rounds_sub_cent_amounts(self):
        # Real amounts seen from Plaid Sandbox's dynamic user.
        self.assertEqual(dollars_to_cents(Decimal("9.7853775")), 979)
        self.assertEqual(dollars_to_cents(Decimal("595.10675")), 59511)
        # Exact half cents go to the even cent (banker's rounding).
        self.assertEqual(dollars_to_cents(Decimal("1.005")), 100)
        self.assertEqual(dollars_to_cents(Decimal("1.015")), 102)
        self.assertEqual(dollars_to_cents(Decimal("-1.005")), -100)
        with self.assertRaises(ValueError):
            dollars_to_cents(Decimal("NaN"))

    def test_to_transaction_flips_sign_and_maps_fields(self):
        purchase = to_transaction(plaid_txn("t1", "4.50"))
        self.assertEqual(purchase.amount, -450)  # Plaid +4.50 out -> our -450
        self.assertEqual(purchase.description, "Starbucks")
        self.assertEqual(purchase.category, "FOOD_AND_DRINK")
        self.assertEqual(purchase.date, date(2026, 10, 20))

        deposit = to_transaction(plaid_txn("t2", "-2500.00"))
        self.assertEqual(deposit.amount, 250000)  # Plaid -2500 in -> our +250000

    def test_to_transaction_fallbacks(self):
        txn = to_transaction(plaid_txn("t1", "1.00", merchant_name=None, personal_finance_category=None))
        self.assertEqual(txn.description, "Raw Bank Name")
        self.assertEqual(txn.category, "UNCATEGORIZED")

    def test_to_transaction_accepts_usd_and_missing_currency(self):
        self.assertEqual(to_transaction(plaid_txn("t1", "4.50", iso_currency_code="USD")).amount, -450)
        self.assertEqual(to_transaction(plaid_txn("t2", "4.50")).amount, -450)  # no code -> USD

    def test_to_transaction_rejects_other_currencies(self):
        with self.assertRaises(ValueError):
            to_transaction(plaid_txn("jpy", "1500", iso_currency_code="JPY"))
        with self.assertRaises(ValueError):
            to_transaction(plaid_txn("kwd", "1.234", iso_currency_code="KWD"))
        with self.assertRaises(ValueError):  # crypto: iso code is null
            to_transaction(plaid_txn("btc", "0.01", iso_currency_code=None, unofficial_currency_code="BTC"))

    def test_to_transaction_ignores_non_object_category(self):
        txn = to_transaction(plaid_txn("t1", "1.00", personal_finance_category="FOOD"))
        self.assertEqual(txn.category, "UNCATEGORIZED")


class SummaryTests(unittest.TestCase):
    def test_summary_is_sanitized(self):
        result = SyncResult(
            added=[plaid_txn("t1", "4.50"), plaid_txn("t2", "-100.00", date="2026-10-22")],
            next_cursor="secret-cursor-value",
            pages=1,
        )
        summary = format_summary(result, PlaidItem("item-xyz-9876", ACCESS_TOKEN))

        self.assertIn("Added:     2", summary)
        self.assertIn("$95.50", summary)  # +100.00 in, -4.50 out
        self.assertIn("…9876", summary)  # masked item id
        self.assertIn("…0001", summary)  # masked account id
        for hidden in (ACCESS_TOKEN, "item-xyz-9876", "acct-checking-0001", "secret-cursor-value"):
            self.assertNotIn(hidden, summary)
        # The per-transaction list (after "Most recent") is newest first.
        listing = summary[summary.index("Most recent"):]
        self.assertLess(listing.index("2026-10-22"), listing.index("2026-10-20"))

    def test_summary_skips_unconvertible_transactions(self):
        result = SyncResult(added=[
            plaid_txn("good", "4.50"),
            plaid_txn("not-a-number", "NaN"),  # not a finite amount -> ValueError
            plaid_txn("no-name", "1.00", merchant_name=None, name=None),  # -> ValidationError
        ])
        summary = format_summary(result, PlaidItem("item-1234", ACCESS_TOKEN))
        self.assertIn("Skipped:   2", summary)
        self.assertIn("-$4.50", summary)  # the good one still counted

    def test_summary_skips_non_usd_and_malformed_rows(self):
        result = SyncResult(added=[
            plaid_txn("good", "4.50"),
            plaid_txn("jpy", "1500", iso_currency_code="JPY"),
            "not-a-dict",  # AttributeError on .get -> skipped, not a crash
        ])
        summary = format_summary(result, PlaidItem("item-1234", ACCESS_TOKEN))
        self.assertIn("Skipped:   2", summary)
        self.assertIn("(positive = money in): -$4.50", summary)  # yen not added in

    def test_summary_marks_pending_and_excludes_them_from_net(self):
        result = SyncResult(added=[
            plaid_txn("posted", "4.50"),
            plaid_txn("pending", "20.00", pending=True, date="2026-10-21"),
        ])
        summary = format_summary(result, PlaidItem("item-1234", ACCESS_TOKEN))
        self.assertIn("Added:     2 (1 pending)", summary)
        self.assertIn("(positive = money in): -$4.50", summary)  # pending $20 not counted
        self.assertIn("P 2026-10-21", summary)


class FormatCentsTests(unittest.TestCase):
    def test_format_cents_is_shared_with_main(self):
        import main
        from backend.transaction import format_cents

        self.assertIs(main.format_cents, format_cents)  # one implementation
        self.assertEqual(format_cents(-120000), "-$1,200.00")
        self.assertEqual(format_cents(5), "$0.05")


class EnvFileTests(unittest.TestCase):
    def test_load_env_file_does_not_override_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('# comment\nFT_TEST_A="from-file"\nFT_TEST_B=b=c\n\nFT_TEST_C=file\n')
            # patch.dict restores os.environ after the block.
            with mock.patch.dict(os.environ, {"FT_TEST_C": "already-set"}):
                load_env_file(env_path)
                self.assertEqual(os.environ["FT_TEST_A"], "from-file")  # quotes removed
                self.assertEqual(os.environ["FT_TEST_B"], "b=c")  # split on first '='
                self.assertEqual(os.environ["FT_TEST_C"], "already-set")

    def test_load_env_file_handles_export_comments_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "export FT_TEST_EXPORT=exported\n"
                "FT_TEST_COMMENT=abc123  # sandbox secret\n"
                "FT_TEST_HASH=abc#123\n"
                "FT_TEST_QUOTED='keep # this'\n"
                "FT_TEST_UNMATCHED=\"abc'\n"
            )
            names = ["FT_TEST_EXPORT", "FT_TEST_COMMENT", "FT_TEST_HASH", "FT_TEST_QUOTED", "FT_TEST_UNMATCHED"]
            with mock.patch.dict(os.environ, {}):
                for name in names:
                    os.environ.pop(name, None)
                load_env_file(env_path)
                self.assertEqual(os.environ["FT_TEST_EXPORT"], "exported")
                self.assertNotIn("export FT_TEST_EXPORT", os.environ)
                self.assertEqual(os.environ["FT_TEST_COMMENT"], "abc123")
                self.assertEqual(os.environ["FT_TEST_HASH"], "abc#123")
                self.assertEqual(os.environ["FT_TEST_QUOTED"], "keep # this")
                self.assertEqual(os.environ["FT_TEST_UNMATCHED"], "\"abc'")  # not a matching pair

    def test_load_env_file_quoted_value_with_comment_and_bad_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                'FT_TEST_QC="sandbox"  # only sandbox\n'
                "FT_TEST_SQ='a # b' # note\n"
                "=no-name\n"
                "export =also-no-name\n"
                "1BAD=starts-with-digit\n"
                "FT_TEST_AFTER=still-loaded\n"
            )
            with mock.patch.dict(os.environ, {}):
                for name in ("FT_TEST_QC", "FT_TEST_SQ", "FT_TEST_AFTER"):
                    os.environ.pop(name, None)
                load_env_file(env_path)  # must not raise OSError
                self.assertEqual(os.environ["FT_TEST_QC"], "sandbox")  # no quote marks
                self.assertEqual(os.environ["FT_TEST_SQ"], "a # b")
                self.assertNotIn("1BAD", os.environ)
                self.assertEqual(os.environ["FT_TEST_AFTER"], "still-loaded")


if __name__ == "__main__":
    unittest.main()
