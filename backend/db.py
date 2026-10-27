"""MySQL (Aiven Cloud) connection helpers using PyMySQL. See docs/phase5.md."""

import os
from pathlib import Path

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from backend.config import ConfigError, load_env_file, project_path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
REQUIRED_SETTINGS = (
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE", "MYSQL_SSL_CA",
)

# Common MySQL error codes, with a hint about what to check.
ERROR_HINTS = {
    1045: "wrong MYSQL_USER or MYSQL_PASSWORD",
    1049: "that database doesn't exist on the server (create it in the Aiven console)",
    2003: "can't reach MYSQL_HOST:MYSQL_PORT (is the Aiven service running?)",
    2005: "unknown MYSQL_HOST",
}


class DatabaseError(Exception):
    """A database call failed. The message never contains the password."""


def describe_error(exc: pymysql.MySQLError) -> str:
    """A short, password-free description of a PyMySQL error: class, code and hint."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    text = type(exc).__name__ + (f" {code}" if code is not None else "")
    hint = ERROR_HINTS.get(code)
    return f"{text}: {hint}" if hint else text


def is_configured() -> bool:
    """True when .env (or the environment) has MySQL settings."""
    load_env_file()
    return bool(os.environ.get("MYSQL_HOST"))


def connect(database: str | None = None) -> Connection:
    """Open an SSL connection to MySQL. ``database`` overrides MYSQL_DATABASE (e.g. for tests).

    Raises:
        ConfigError: A setting is missing or the CA certificate file doesn't exist.
        DatabaseError: MySQL refused or couldn't be reached.
    """
    load_env_file()
    missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"Missing database settings in .env: {', '.join(missing)}")

    ca_path = project_path(os.environ["MYSQL_SSL_CA"])
    if not ca_path.is_file():
        raise ConfigError(f"MYSQL_SSL_CA file not found: {ca_path}")
    try:
        port = int(os.environ["MYSQL_PORT"])
    except ValueError:
        raise ConfigError("MYSQL_PORT must be a number") from None

    host = os.environ["MYSQL_HOST"]
    try:
        return pymysql.connect(
            host=host,
            port=port,
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=database or os.environ["MYSQL_DATABASE"],
            ssl={"ca": str(ca_path)},  # verifies Aiven's certificate and hostname
            cursorclass=DictCursor,
            autocommit=False,
            charset="utf8mb4",
            connect_timeout=10,
        )
    except pymysql.MySQLError as exc:
        raise DatabaseError(f"Could not connect to MySQL at {host}:{port} ({describe_error(exc)})") from None


def init_schema(conn: Connection) -> None:
    """Create the tables if they don't exist yet."""
    statements = [part.strip() for part in SCHEMA_PATH.read_text().split(";")]
    with conn.cursor() as cursor:
        for statement in statements:
            sql = "\n".join(
                line for line in statement.splitlines() if not line.strip().startswith("--")
            ).strip()
            if sql:
                cursor.execute(sql)
    conn.commit()
