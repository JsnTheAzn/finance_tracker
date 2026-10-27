# Continuous Integration (GitHub Actions)

## Why
Before this, tests ran only when someone remembered to run them locally. Now GitHub runs the whole suite on every push to `main` and on every pull request into `main`, so a change that breaks something is flagged straight away, before Phase 6 adds more moving parts.

## What was built
| File | What it does |
|---|---|
| `.github/workflows/tests.yml` | The workflow GitHub Actions runs. One job, `test`, on an Ubuntu machine. |

**The steps, in order:**
1. **`actions/checkout`:** downloads the repository onto the CI machine.
2. **`actions/setup-python`:** installs Python 3.14 (the version used locally). `cache: pip` reuses downloaded packages between runs, based on `requirements.txt`.
3. **Install dependencies:** `pip install -r requirements.txt`, the same pinned versions as locally.
4. **Run tests:** `python -m unittest discover -s tests -v`, the same command as locally.

`permissions: contents: read` gives the workflow read-only access to the repository, because it never needs to write anything.

**Libraries:** none added. GitHub Actions is part of GitHub, and the tests use `unittest` from the standard library.

## What CI does *not* test
- **MySQL:** the 30 tests in `tests/test_mysql.py` are **skipped** in CI. They only run when `MYSQL_*` and `MYSQL_TEST_DATABASE` are set, and CI has neither a `.env` file nor any secrets. Run them locally against Aiven before committing database changes.
- **Live Plaid:** every Plaid test uses a fake Plaid server (`httpx.MockTransport`), so CI never calls Plaid and needs no Plaid credentials.

So a green run shows **"OK (skipped=30)"**. That is expected; a failure or an error is not.

**Why not run MySQL in CI too?**
- Using Aiven from CI would mean storing cloud database credentials in GitHub, and two runs at the same time would empty each other's `finance_test` tables.
- A throwaway MySQL in Docker is possible, but it needs an SSL certificate made for each run, which adds setup for little gain right now.

Either can be added later.

## How it was tested
The CI run was simulated locally:
1. Copied only the files Git tracks (so no `.env` and no `certs/`) into a scratch folder.
2. Created a fresh virtual environment with Python 3.14 and installed `requirements.txt`.
3. Ran the test command. Result: `Ran 138 tests … OK (skipped=30)`.

After pushing, check the **Actions** tab on GitHub: the run should show a green tick.

## Optional next step (done in the GitHub website)
**Branch protection:** under *Settings → Branches*, you can require the `test` check to pass before a pull request can be merged into `main`.
