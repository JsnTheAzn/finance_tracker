"""Backend package for the Finance Tracker.

Phase 1 contains the core domain logic only:
    - transaction.py          -> Pydantic models for a transaction
                                 (TransactionCreate and Transaction)
    - transaction_manager.py  -> in-memory storage and operations on Transactions

The only third-party library used so far is Pydantic (for data validation).
FastAPI and Plaid will be layered on top of these modules in later phases.
"""
