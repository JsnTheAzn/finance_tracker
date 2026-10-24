"""Backend package for the Finance Tracker.

This package holds the core domain logic:
    - transaction.py          -> Pydantic models for a transaction
                                 (TransactionCreate and Transaction)
    - transaction_manager.py  -> in-memory storage and operations on Transactions

The only third-party library used here is Pydantic (for data validation).
The FastAPI layer lives in main.py at the project root; Plaid comes later.
"""
