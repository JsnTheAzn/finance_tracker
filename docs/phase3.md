# Phase 3: Plaid Sandbox integration

## What was built
`backend/plaid_services.py`: a Sandbox-only Plaid client, plus a command that runs the whole flow:
```bash
.venv/bin/python -m backend.plaid_services
```
1. **`create_sandbox_public_token`** (`POST /sandbox/public_token/create`). Simulates a user linking a bank. In production this happens in the browser through Plaid Link; Sandbox skips the UI. The defaults are the test bank `ins_109508` ("First Platypus Bank") and the test user `user_transactions_dynamic`, which has realistic checking, credit card and loan history.
2. **`exchange_public_token`** (`POST /item/public_token/exchange`). Trades the short-lived `public_token` for a long-lived **`access_token`** plus an `item_id`. The access token is a secret.
3. **`sync_transactions`** (`POST /transactions/sync`). Fetches transactions page by page (details below).
4. **`to_transaction`** / **`format_summary`**. Converts Plaid transactions into our `Transaction` and prints a sanitized summary.

## How `/transactions/sync` works
Plaid returns *changes since a bookmark* (the **cursor**), not "all transactions":
- Keep calling with `next_cursor` until `has_more` is false, then save the last cursor. The next sync with that cursor returns only what changed since.
- Each page has `added`, `modified` and `removed` lists.
- **Not ready yet.** A freshly linked Item returns empty lists and an **empty `next_cursor`** while Plaid is still pulling data. On a first sync we poll every 2s, for up to 30s, and then raise `DATA_NOT_READY`, so "not ready" is never mistaken for "no transactions".
  - Tested against the live Sandbox: the status field can't be trusted for this. It reports `INITIAL_UPDATE_COMPLETE`, and even `HISTORICAL_UPDATE_COMPLETE`, for a second or two before the data arrives, so only the empty cursor is checked.
  - **Assumption (not yet observed):** a ready Item with zero transactions returns a non-empty cursor. If that turns out to be wrong, such an Item would hit the 30s timeout.
- **`TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION`.** If data changes mid-paging, the partial results are thrown away and paging restarts from the original cursor, as Plaid's docs require (up to 3 times).

## Converting Plaid data
- **Sign flip:** Plaid uses positive = money *out*; we use positive = money *in*.
- **Dollars → cents:** amounts are parsed as `Decimal` (never float) and converted with `dollars_to_cents`.
  - Sandbox's dynamic user returns some USD amounts with up to 7 decimals (e.g. `9.7853775`). Rejecting them dropped about 1 in 10 transactions, so amounts are rounded **half-even** ("banker's rounding"): `1.005 → 100`, `1.015 → 102`. This avoids pushing totals upward over many transactions.
- **USD only:** cents only make sense for 2-decimal currencies, and the app shows `$`. A non-USD `iso_currency_code`, or an `unofficial_currency_code` (crypto), is refused, so ¥1500 can never become $1,500.00. A transaction with neither code is treated as USD.
- **Description / category:** `merchant_name`, falling back to `name`. `personal_finance_category.primary`, falling back to `UNCATEGORIZED`.
- **Bad rows are skipped and counted,** never crashing the whole summary.

## Security
- **Credentials:** the client ID and secret go only in the `PLAID-CLIENT-ID` / `PLAID-SECRET` **headers**, never in request bodies, and are never printed. `PlaidClient` and `PlaidItem` hide the secret and the token from `repr()`.
- **Safe errors:** every failure is a `PlaidError` built only from Plaid's documented error fields (or a network exception's class name), and raised `from None` so the original exception, which holds the request headers, isn't chained. A reply that is missing fields, isn't JSON, or isn't an object also becomes a `PlaidError`, rather than a `KeyError` whose traceback could expose local variables.
- **Sandbox only:** `PLAID_ENV` must be `sandbox`, so the code can't touch real banks by accident.
- **No endpoint exposes tokens:** the Plaid code is separate from the FastAPI routes, and no endpoint returns an access token.
- **Sanitized summary:** item and account IDs are masked to their last 4 characters, and no tokens or cursors are printed.

## Configuration (`.env`)
Copy `.env.example` to `.env` (git-ignored) and fill in `PLAID_CLIENT_ID` and the **Sandbox** `PLAID_SECRET` from https://dashboard.plaid.com/developers/keys.

`load_env_file` is a small stand-in for python-dotenv:
- It skips comments and lines with invalid names (e.g. `=abc`, which would otherwise crash `os.environ`).
- It accepts `export KEY=...`.
- It handles quotes and trailing ` # comments` (`KEY="abc"  # note` → `abc`).
- It never overrides a variable that is already set.

A secret identical to the client ID is caught with a clear `SECRET_MATCHES_CLIENT_ID` error. That mistake happened, and Plaid only answers it with a generic `INVALID_API_KEYS`.

## Testing
```bash
.venv/bin/python -m unittest tests.test_plaid_services -v
```
The tests run offline: `httpx.MockTransport` hands every request to a fake Plaid (`FakePlaid` in the test file), so no keys or network are needed. They check that secrets never appear in errors, request bodies or output.

## Docs followed
- https://plaid.com/docs/api/sandbox/
- https://plaid.com/docs/api/items/
- https://plaid.com/docs/api/products/transactions/

## Libraries
httpx, plus the standard library (`decimal`, `json`, `re`, `dataclasses`, ...).
