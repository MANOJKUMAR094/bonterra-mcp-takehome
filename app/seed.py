import argparse
import sys
from pathlib import Path

from app.db import get_connection, get_db_path, init_schema

SEED_NONPROFITS = [
    ("np_community_bridge", "Community Bridge"),
    ("np_hope_foundation", "Hope Foundation"),
]

SEED_STAFF = [
    # Community Bridge
    ("cw_cb_1", "np_community_bridge", "Maya Lin", "case_worker"),
    ("pm_cb_1", "np_community_bridge", "David Ortiz", "program_manager"),
    ("fin_cb_1", "np_community_bridge", "Priya Nair", "finance_staff"),
    # Hope Foundation
    ("cw_hf_1", "np_hope_foundation", "Samir Patel", "case_worker"),
    ("pm_hf_1", "np_hope_foundation", "Elena Rostova", "program_manager"),
    ("fin_hf_1", "np_hope_foundation", "Jordan Lee", "finance_staff"),
]

SEED_PARTICIPANTS = [
    ("part_cb_1", "np_community_bridge", "Marcus Vance", "active"),
    ("part_cb_2", "np_community_bridge", "Amina Yusuf", "active"),
    ("part_cb_inactive", "np_community_bridge", "Carlos Mendez", "inactive"),
    ("part_hf_1", "np_hope_foundation", "Hannah Brooks", "active"),
    ("part_hf_2", "np_hope_foundation", "Devon Brooks", "active"),
]

SEED_FUNDS = [
    # (id, nonprofit_id, name, allocation_cents, committed_cents, paid_cents)
    ("fund_cb_housing", "np_community_bridge", "Emergency Rent & Housing", 1_000_000, 0, 0),   # $10,000.00
    ("fund_cb_utilities", "np_community_bridge", "Utilities & Transit Relief", 250_000, 0, 0), # $2,500.00
    ("fund_hf_general", "np_hope_foundation", "Rapid Family Stabilization", 500_000, 0, 0),    # $5,000.00
]

SEED_REQUESTS = [
    (
        "req_cb_seed_1",
        "np_community_bridge",
        "part_cb_2",
        "fund_cb_utilities",
        15_000,  # $150.00
        "Past-due electric bill shutoff prevention",
        "Pending",
        "cw_cb_1",
        "2026-11-26T09:00:00Z",
    ),
    (
        "req_hf_seed_1",
        "np_hope_foundation",
        "part_hf_1",
        "fund_hf_general",
        40_000,  # $400.00
        "Emergency security deposit assistance",
        "Pending",
        "cw_hf_1",
        "2026-11-26T09:15:00Z",
    ),
]


def reset_and_seed(db_path: Path | str | None = None) -> Path:
    target = Path(db_path) if db_path else get_db_path()
    conn = get_connection(target)
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE;")
        conn.executemany("INSERT INTO nonprofits (id, name) VALUES (?, ?);", SEED_NONPROFITS)
        conn.executemany(
            "INSERT INTO staff (id, nonprofit_id, name, role) VALUES (?, ?, ?, ?);",
            SEED_STAFF,
        )
        conn.executemany(
            "INSERT INTO participants (id, nonprofit_id, full_name, status) VALUES (?, ?, ?, ?);",
            SEED_PARTICIPANTS,
        )
        conn.executemany(
            """
            INSERT INTO funds (id, nonprofit_id, name, allocation_cents, committed_cents, paid_cents)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            SEED_FUNDS,
        )
        conn.executemany(
            """
            INSERT INTO assistance_requests (
                id, nonprofit_id, participant_id, fund_id, amount_cents, purpose,
                status, created_by_staff_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            SEED_REQUESTS,
        )
        conn.execute("COMMIT;")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize or reset the Emergency Assistance SQLite database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        required=True,
        help="Drop existing tables and re-seed synthetic fixture data.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Optional custom SQLite file path (defaults to ASSISTANCE_DB_PATH or ./assistance.db).",
    )
    args = parser.parse_args()
    if args.reset:
        path = reset_and_seed(args.db)
        print(f"Database reset and seeded successfully at: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
