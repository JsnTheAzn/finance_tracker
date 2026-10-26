# Phase 2: FastAPI endpoints

## What was built
`main.py` creates a FastAPI `app` that exposes `TransactionManager` over HTTP:

| Action (operation_id) | HTTP | Success |
|---|---|---|
| `getAllTransaction` | `GET /transactions` | 200 |
| `getTransaction` | `GET /transactions/{id}` | 200 |
| `addTransaction` | `POST /transactions` | 201 |
| `deleteTransaction` | `DELETE /transactions/{id}` | 200 (returns what was removed) |

The camelCase action names are set as each route's `operation_id`. That's the name shown in `/docs`, and the name a generated frontend client would use. The Python functions themselves stay snake_case.

## How it works
- **Validation comes for free.** The body of `POST /transactions` is a `TransactionCreate`, and `{id}` is typed as `UUID`. FastAPI returns **422** for a missing or wrong-typed field (e.g. `"amount": 4.5`) or an invalid UUID before our code runs.
- **404s:** the manager raises `KeyError` for an unknown ID, and the endpoint turns that into `HTTPException(404)` using the shared `_not_found` helper.
- **`response_model`** tells FastAPI how to turn a `Transaction` into JSON (UUID → string, date → `"YYYY-MM-DD"`) and documents the response shape.
- **Dependency injection:** endpoints get the manager through `Depends(get_manager)` instead of touching the module-level `_manager`. Tests swap in a fresh manager with `app.dependency_overrides[get_manager]`, and the database phase can return a DB-backed manager without changing any endpoint.
- **Storage is still in memory,** so data is lost when the server stops.
- **The demo is separate.** `python main.py --demo` uses its own manager, so it never touches server data. `uvicorn` is imported only when starting the server, so importing `main` (in tests) stays light.

## How to run
```bash
.venv/bin/python main.py
```
Then open http://127.0.0.1:8000/docs.
```bash
.venv/bin/python -m unittest tests.test_api -v
```

## Libraries
FastAPI, uvicorn (the server), and httpx (required by FastAPI's `TestClient`).
