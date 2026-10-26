# Phase 4: Loading Plaid transactions into TransactionManager

## Why
Phase 3 printed Plaid transactions but stored nothing. It also gave every transaction a brand-new UUID and threw away Plaid's own ID, so loading the same sync twice would have duplicated everything, and Plaid's `modified` and `removed` lists couldn't be applied.

## What was built
- **`Transaction` has two new fields:**
  - `external_id`: Plaid's `transaction_id`. It is `None` for transactions added by hand.
  - `pending`: `True` until the bank posts the charge.

  Both are on `Transaction` only, not `TransactionCreate`. API responses show them, but a client can't set them through `addTransaction`; sent values are ignored.
- **`TransactionManager.apply_sync(added, modified, removed, replaces=None)`:** applies one sync and returns `SyncCounts(added, updated, removed)`.
- **`calculate_net_change(include_pending=False)`:** a total that leaves out charges that may still change. The default (`True`) keeps the old behaviour.
- **`plaid_services.to_sync_changes(result)`** turns a Plaid sync into a `SyncChanges` object. It is the only place that knows Plaid's data shapes, so the manager stays Plaid-agnostic.
- **`load_into_manager`:** the demo now loads the sync into a manager, then **re-syncs the same data** and checks that nothing new was stored.

## How `apply_sync` works
1. **Validate first, change nothing on failure.** Every incoming item must be a `Transaction` (otherwise `TypeError`) with an `external_id` (otherwise `ValueError`). A new transaction whose UUID is already used by a *different* stored transaction is also rejected (`ValueError`), instead of silently overwriting it.
2. **Build a temporary lookup** `{external_id: our UUID}` from the stored transactions.
3. **Upsert** (insert or update) each added or modified transaction:
   - If its `external_id` is already stored, replace it **but keep our existing UUID**, so `/transactions/{id}` links keep working. That's why a second identical sync reports `added=0, updated=N`.
   - If it's new, store it.
   - The store always keeps a **copy**, so changing the object you passed in later can't change stored data.
4. **Remove** by `external_id`. Unknown IDs are ignored, because "already gone" isn't an error.
5. **Transactions added by hand** (`external_id=None`) are never touched.

### Pending → posted
When a pending charge posts, Plaid sends a **new** transaction (new `transaction_id`, `pending_transaction_id` = the old one) and lists the pending one under `removed`.
- `to_sync_changes` turns that link into `replaces = {posted_id: pending_id}`.
- `apply_sync` then **updates the pending record in place**: same UUID, new `external_id`, final amount, `pending=False`.
- Because the record was already re-keyed, the later "remove pending_id" step finds nothing to delete.

The result: one transaction whose UUID never changes, and no double counting.

## Design decision: no permanent second dictionary
The first plan kept a second `external_id → UUID` dict next to the main one. It was dropped because:
- every `add` and `delete` would have had to keep it in sync, and
- the database phase replaces it anyway, with a `UNIQUE` constraint on `external_id` plus an upsert (`INSERT … ON CONFLICT (external_id) DO UPDATE`).

Instead, `apply_sync` builds the lookup in one pass each time it runs. What carries over to the database is the **method itself** (inputs, rules and return counts), and the tests only use public methods, so they can run against the DB-backed manager later.

## Review fixes (before commit)
- **UUID collision:** a new transaction whose UUID is already in use now raises instead of overwriting.
- **Stored copies:** insert used to store the caller's own object while update stored a copy. Both now store copies.
- **A real idempotency check:** the demo's second sync used to reuse the same objects (same UUIDs), so its "OK" proved nothing. It now converts again (new UUIDs), just like a real re-sync.
- **Clear errors:** passing raw Plaid dicts to `apply_sync` gives a clear `TypeError`.
- **Pending UUIDs:** `pending_transaction_id` is now used, so a posted charge keeps its pending UUID.
- **Readiness check:** the "empty bank" shortcut from Phase 3 was reverted. Live Sandbox runs showed `HISTORICAL_UPDATE_COMPLETE` arriving before the data, which made syncs return 0 transactions (see `phase3.md`).

## Results (live Sandbox)
```
First sync:   SyncCounts(added=331, updated=0, removed=0)
Second sync:  SyncCounts(added=0, updated=331, removed=0)  (same data again)
Stored:       331 (14 pending)
OK: re-syncing the same data stored nothing new
```
The posted-only net change matches Plaid's summary.

## How to run
```bash
.venv/bin/python -m backend.plaid_services
.venv/bin/python -m unittest discover -s tests -v
```

## Libraries
No new ones: Pydantic, httpx and the standard library.
