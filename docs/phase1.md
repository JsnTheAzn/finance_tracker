# Phase 1: Transaction model and TransactionManager

## What was built
- `backend/transaction.py`: Pydantic models for a single transaction.
- `backend/transaction_manager.py`: an in-memory store that adds, gets, deletes and totals transactions.
- `main.py --demo`: a print-statement demo of the steps above.

## How it works

### Two models: `TransactionCreate` and `Transaction`
- **`TransactionCreate`** holds what a client sends: `amount`, `description`, `category` and `date`.
- **`Transaction`** is `TransactionCreate` plus a server-generated `id` (a UUID). It's what gets stored and returned.

Splitting them means an API caller can never choose the ID on create, while stored transactions always have one. `Transaction.from_create(data)` turns client data into a stored transaction with a fresh UUID.

### Money is a signed integer number of cents
`$12.34` is stored as `1234`. Floats are never used for money, because binary floating point can't represent most decimal values exactly (`0.1 + 0.2 != 0.3`), which causes rounding drift.
- **Positive** = money **in** (income, refunds)
- **Negative** = money **out** (purchases, bills)

`format_cents(-120000)` turns cents into `'-$1,200.00'` for display, using integer math only.

### UUIDs
Each transaction gets a `uuid4()`, which is 122 random bits, so collisions practically never happen. IDs can also be created without asking a database for the "next" number. `Field(default_factory=uuid4)` runs once per object, so every transaction gets its own UUID.

### Strict validation
`model_config = ConfigDict(strict=True)` stops Pydantic from "helpfully" converting values. For example, it rejects `"450"` or `4.5` for `amount` instead of silently accepting them.

The one exception is `date`. JSON has no date type, so dates arrive as `"2026-10-21"`. A small before-validator (`_parse_iso_date`) converts ISO strings to real dates, and anything else (numbers, `"10/21/2026"`) is still rejected.

### Dictionary storage, keyed by UUID
`TransactionManager` keeps `dict[UUID, Transaction]`. Python dicts are hash tables, so add, get and delete by ID are O(1) on average, no matter how many transactions are stored.
- `add_transaction` rejects non-Transactions (`TypeError`) and duplicate IDs (`ValueError`), because silently overwriting would lose data.
- `get_transaction` / `delete_transaction` raise `KeyError` for unknown IDs, which the API later turns into a 404.
- `get_all_transactions` returns a new list, so callers can't change the store by accident.
- `calculate_net_change` sums the signed amounts. For example, +250000 (paycheck) − 450 (coffee) = 249550 cents.

## How to run
```bash
.venv/bin/python main.py --demo
.venv/bin/python -m unittest tests.test_transactions -v
```

## Libraries
Pydantic, plus the standard library (`datetime`, `uuid`).
