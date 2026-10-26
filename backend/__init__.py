"""Backend package for the Finance Tracker.

This package holds the core domain logic:
    - transaction.py          -> Pydantic models for a transaction
                                 (TransactionCreate and Transaction)
    - transaction_manager.py  -> in-memory storage and operations on Transactions
    - plaid_services.py       -> Plaid Sandbox client: link a fake bank, exchange
                                 tokens, sync transactions (uses httpx)

Third-party libraries used here: Pydantic (data validation) and httpx (HTTP
calls to Plaid). The FastAPI layer lives separately in main.py at the
project root.
"""
