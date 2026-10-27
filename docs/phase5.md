# Phase 5: MySQL persistence (Aiven Cloud)

## Why
Before this phase everything lived in memory:
- Restarting the server lost every transaction.
- Every Plaid run linked a brand-new fake bank, because the access token and sync cursor were thrown away.

The database comes before the UI because a UI over data that disappears on restart isn't useful, and the Plaid Link flow a UI needs (Phase 6) has to keep the token and cursor somewhere.

## What was built
| File | What it does |
|---|---|
| `backend/config.py` | `.env` loading, moved out of `plaid_services.py` so Plaid and the database share it. Also `ConfigError` and `project_path`. |
| `backend/schema.sql` | The two tables, as plain `CREATE TABLE IF NOT EXISTS` statements. |
| `backend/db.py` | `connect()` (SSL connection to MySQL), `init_schema()` and `is_configured()`. |
| `backend/mysql_transaction_manager.py` | `MySQLTransactionManager`: the same methods and rules as the in-memory `TransactionManager`, but backed by SQL. |
| `backend/plaid_item_store.py` | `PlaidItemStore`: saves linked banks with the access token **encrypted**, plus each bank's sync cursor. |
| `backend/sync_service.py` | `sync_item()`: resume from the saved cursor → apply the changes → save the new cursor. Also the `main()` for the MySQL command. |
| `main.py` | `get_manager()` gives each request a MySQL manager (connection closed afterwards) when MySQL is configured, and the in-memory one otherwise. Tables are created on server start. |

**Libraries:**
- `pymysql`: the MySQL driver. Pure Python, and you write the SQL yourself.
- `cryptography`: Fernet encryption for the Plaid tokens.

## One-time Aiven setup
1. **Create the service:** at https://console.aiven.io, create a **MySQL** service on the free plan.
2. **Copy the connection details:** on the service's **Overview** page, copy Host, Port, User (`avnadmin`), Password and Database (`defaultdb`) into `.env`. See `.env.example` for the variable names.
3. **Download the CA certificate:** download it on the same page and save it as `certs/aiven-ca.pem`.
   - Aiven only accepts encrypted (SSL/TLS) connections.
   - The CA certificate lets the client check that the server really is your Aiven service and not something pretending to be it. PyMySQL verifies both the certificate and the hostname.
   - `certs/` is git-ignored.
4. **Create a test database:** on the **Databases** tab, create `finance_test` and set `MYSQL_TEST_DATABASE=finance_test`. Tests empty its tables on every run, and they refuse to run if it has the same name as `MYSQL_DATABASE`.
5. **Generate the encryption key** and set it as `PLAID_TOKEN_KEY` in `.env`:
   ```bash
   .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

Leaving `MYSQL_HOST` empty keeps everything in memory, as before.

## The schema
**`transactions`:**

| Column | Type | Why |
|---|---|---|
| `id` | `CHAR(36)` primary key | The UUID as text (`3f2b…-…`). |
| `seq` | `BIGINT AUTO_INCREMENT` (unique) | Remembers insertion order, so `get_all_transactions` returns rows "in the order added", like the dict did. `created_at` only has 1-second precision, so it can't break ties. |
| `amount` | `BIGINT` | Signed cents. `BIGINT` holds up to ±9.2 quintillion cents. |
| `description` / `category` | `VARCHAR(512)` / `VARCHAR(100)` | The Pydantic model has the same `max_length`, so the API returns 422 instead of a database error. |
| `date` | `DATE` | A calendar date, with no time zone involved. |
| `external_id` | `VARCHAR(255) COLLATE utf8mb4_bin`, **`UNIQUE`** | Plaid's `transaction_id`. `UNIQUE` is the database version of "never store the same Plaid transaction twice". MySQL allows many `NULL`s in a unique column, so manual transactions are fine. **`utf8mb4_bin`** makes the comparison case-sensitive: MySQL 8 compares text case-insensitively by default, which would treat Plaid IDs `abcD` and `abcd` as the same. |
| `pending` | `BOOLEAN` | Stored as `TINYINT` 0/1, and converted back to `bool`. |
| `created_at` / `updated_at` | `TIMESTAMP` | Filled in automatically, which helps when debugging. |

**`plaid_items`:**
- `item_id` (primary key, `utf8mb4_bin`) and `institution_id`.
- `access_token_encrypted`: Fernet ciphertext. It always starts with `gAAAA…`, and it is never the raw token.
- `sync_cursor`: where the next `/transactions/sync` resumes. `NULL` means "from the beginning".

Everything uses `utf8mb4`, so emoji and accents in merchant names are stored correctly.

## How `apply_sync` maps to SQL
`MySQLTransactionManager.apply_sync` follows the in-memory version step by step, so the two are easy to compare. It all runs inside **one database transaction**: `COMMIT` if everything succeeds, `ROLLBACK` on any error, so a sync is never half-saved.
1. **Validate the input:** the same `validate_sync_input` function both managers share.
2. **Find existing rows:** `SELECT id, external_id … WHERE external_id IN (…) FOR UPDATE` replaces the in-memory temporary lookup. `FOR UPDATE` locks those rows until commit, so two syncs can't race.
3. **Check for UUID collisions:** `SELECT id … WHERE id IN (…)` for the new transactions.
4. **Insert or update:** `INSERT` new rows. Otherwise `UPDATE … WHERE id = existing_id`, which keeps the UUID. A posted transaction that replaces a pending one updates the pending row, including its `external_id`.
5. **Remove:** `DELETE` by id for the removed `external_id`s that exist.

Every value is passed as a **query parameter** (`%s`), never pasted into the SQL string, so data can't be run as SQL (SQL injection).

**One set of tests for both managers.** `ManagerContract` in `tests/test_transactions.py` holds the manager tests. `InMemoryManagerTests` and `MySQLManagerTests` both inherit it, so the same tests prove both managers behave identically. For the same reason, `add_transaction` now refuses a duplicate `external_id` in both managers.

## Syncing: why the cursor is saved last
`sync_item` does four things in order:
1. Load the item and its saved cursor.
2. Call `/transactions/sync` from that cursor.
3. `apply_sync` the changes (committed).
4. Save the new cursor.

If the program crashes after step 3 but before step 4, the next run fetches the same changes again from the old cursor. That's harmless, because `apply_sync` is idempotent. Saving the cursor *first* would be the dangerous order: a crash would lose those transactions forever.

```bash
.venv/bin/python -m backend.plaid_services
```
This reuses the saved bank and syncs only the changes since the last run.
```bash
.venv/bin/python -m backend.plaid_services --relink
```
This links a new Sandbox bank first.

## Encryption key
- **Why encrypt:** the access token gives read access to the bank account. Encrypting it means a leaked database dump (or someone browsing the Aiven console) can't use it without the key from `.env`.
- **Fernet** is AES encryption plus a tamper check. A wrong or changed key fails loudly (a `ConfigError`) instead of returning garbage.
- **Rotating the key:** generate a new key, set `PLAID_TOKEN_KEY`, then run once with `--relink`; the old encrypted tokens can't be decrypted with the new key. A real key rotation (decrypt with the old key, re-encrypt with the new one) can come before production.

## Errors and secrets
- **Connection failures** become a `DatabaseError` showing only the error class, the code and a hint. For example, `1045: wrong MYSQL_USER or MYSQL_PASSWORD`, or `2003: can't reach MYSQL_HOST:MYSQL_PORT`. The password is never included.
- **Missing settings** are listed by name. A missing certificate file is reported with its path.

## How it was tested
- **Offline (always run):**
  - Database config errors and that the password never leaks.
  - The SSL settings passed to PyMySQL.
  - Schema splitting.
  - Encryption round-trip and wrong/invalid/missing keys.
  - `sync_item` resuming from a saved cursor, and *not* saving the cursor when applying fails.
  - `get_manager`'s in-memory fallback.
  - The 422 response for text longer than the database columns.
- **Against MySQL:** `tests/test_mysql.py` runs the whole `ManagerContract` plus the item store tests (encrypted at rest, cursors, unknown items) and a case-sensitivity check. It is skipped unless `.env` sets `MYSQL_*` and `MYSQL_TEST_DATABASE`.
- **During development** (before Aiven was set up), all 30 MySQL tests passed against a throwaway local MySQL 9.2 server with SSL. The same server was used to:
  - run the full Plaid → MySQL flow twice. Run 1 linked a bank and stored 331 transactions (14 pending). Run 2 reused the saved bank and cursor and found 0 changes.
  - check that the stored token is ciphertext.
  - check that the API lists the synced transactions, and that a transaction added through `POST` is still there after a restart.

- **Against Aiven** (MySQL 8.4, TLS 1.3, certificate verified):
  - All 138 tests pass with none skipped, including the 30 MySQL tests on `finance_test`. With Aiven configured, the suite takes about 20s instead of under 1s, because every test makes network round trips to the cloud.
  - Sync run 1 linked a bank and stored 316 transactions.
  - Run 2 resumed from the saved cursor and got `added=15, updated=316`. Right after linking, Plaid's dynamic test user keeps producing activity: new transactions plus re-sent ones. Because it matches on `external_id`, the result was 331 rows with 331 distinct Plaid IDs, so no duplicates.
  - Run 3 found 0 changes.
  - The stored token is ciphertext (`gAAAAA…`), and no secret, token or password appeared in the output.
  - The API listed the 331 transactions, and a transaction added through `POST` was still there after a restart. That test transaction was deleted afterwards.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Later
- **Connection pooling:** each API request opens its own SSL connection, which is simple but adds latency. A connection pool can come later.
- **A proper key-rotation script** before using real banks.
