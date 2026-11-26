import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "assistance.db"


def get_db_path() -> Path:
    env_path = os.environ.get("ASSISTANCE_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else get_db_path()
    # isolation_level=None puts sqlite3 in autocommit mode so we can manage
    # explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK transactions cleanly.
    conn = sqlite3.connect(str(target), timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


SCHEMA_SQL = """
DROP TABLE IF EXISTS assistance_requests;
DROP TABLE IF EXISTS funds;
DROP TABLE IF EXISTS participants;
DROP TABLE IF EXISTS staff;
DROP TABLE IF EXISTS nonprofits;

CREATE TABLE nonprofits (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
) STRICT;

CREATE TABLE staff (
    id TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofits(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('case_worker', 'program_manager', 'finance_staff'))
) STRICT;

CREATE TABLE participants (
    id TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofits(id),
    full_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))
) STRICT;

CREATE TABLE funds (
    id TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofits(id),
    name TEXT NOT NULL,
    allocation_cents INTEGER NOT NULL CHECK (allocation_cents >= 0),
    committed_cents INTEGER NOT NULL CHECK (committed_cents >= 0),
    paid_cents INTEGER NOT NULL CHECK (paid_cents >= 0),
    CHECK (committed_cents + paid_cents <= allocation_cents)
) STRICT;

CREATE TABLE assistance_requests (
    id TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofits(id),
    participant_id TEXT NOT NULL REFERENCES participants(id),
    fund_id TEXT NOT NULL REFERENCES funds(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    purpose TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Paid')),
    created_by_staff_id TEXT NOT NULL REFERENCES staff(id),
    reviewed_by_staff_id TEXT NULL REFERENCES staff(id),
    paid_by_staff_id TEXT NULL REFERENCES staff(id),
    decision_note TEXT NULL,
    payment_reference TEXT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT NULL,
    paid_at TEXT NULL
) STRICT;

CREATE INDEX idx_staff_nonprofit ON staff(nonprofit_id);
CREATE INDEX idx_participants_nonprofit ON participants(nonprofit_id);
CREATE INDEX idx_funds_nonprofit ON funds(nonprofit_id);
CREATE INDEX idx_requests_nonprofit ON assistance_requests(nonprofit_id);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
