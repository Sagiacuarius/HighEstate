"""Minimal transactional migration runner for the local MVP.

Usage (from the repository root):
  python infra/migrations/runner.py --up
  python infra/migrations/runner.py --down 004

Connection settings use RUNTIME_DB_HOST, RUNTIME_DB_PORT, RUNTIME_DB_NAME,
and RUNTIME_DB_USER. Password handling remains in libpq/pgpass.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
UP = re.compile(r"^(\d{3})_.+\.up\.sql$")


def connection() -> psycopg.Connection:
    names = ("RUNTIME_DB_HOST", "RUNTIME_DB_PORT", "RUNTIME_DB_NAME", "RUNTIME_DB_USER")
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(f"missing database configuration: {', '.join(missing)}")
    return psycopg.connect(
        host=os.environ["RUNTIME_DB_HOST"], port=int(os.environ["RUNTIME_DB_PORT"]),
        dbname=os.environ["RUNTIME_DB_NAME"], user=os.environ["RUNTIME_DB_USER"],
    )


def body(path: Path) -> str:
    # Existing foundation files contain transaction markers. The runner owns
    # the outer transaction so a failed file cannot be recorded partially.
    return re.sub(r"(?im)^\s*(BEGIN|COMMIT)\s*;\s*$", "", path.read_text(encoding="utf-8"))


def ensure_ledger(conn: psycopg.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS migration_ledger (
        version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
    )""")


def apply_all(conn: psycopg.Connection) -> None:
    ensure_ledger(conn)
    applied = {row[0] for row in conn.execute("SELECT version FROM migration_ledger").fetchall()}
    for path in sorted(ROOT.glob("*.up.sql")):
        match = UP.match(path.name)
        assert match
        version = match.group(1)
        if version in applied:
            continue
        require_prerequisites(conn, version)
        conn.execute(body(path))
        conn.execute("INSERT INTO migration_ledger (version) VALUES (%s)", (version,))
        print(f"applied {version} ({path.name})")


PREREQUISITES = {"004": ("001", "002", "003")}


def require_prerequisites(conn: psycopg.Connection, version: str) -> None:
    """Refuse to apply a migration whose prerequisites are not recorded.

    The MVP runner is deliberately minimal: migration files are matched by
    exact filename and applied in numeric order; checksum verification of
    already-applied files is documented as out of scope and not implemented.
    """
    required = PREREQUISITES.get(version, ())
    missing = [name for name in required if not conn.execute(
        "SELECT 1 FROM migration_ledger WHERE version = %s", (name,)).fetchone()]
    if missing:
        raise SystemExit(
            f"refusing to apply {version}: migration_ledger is missing {', '.join(missing)}"
        )


def down_one(conn: psycopg.Connection, version: str) -> None:
    if version != "004":
        raise SystemExit("only destructive --down 004 is supported")
    ensure_ledger(conn)
    if not conn.execute("SELECT 1 FROM migration_ledger WHERE version = %s", (version,)).fetchone():
        print(f"skipped {version}: not recorded")
        return
    path = next(ROOT.glob("004_*.down.sql"))
    conn.execute(body(path))
    conn.execute("DELETE FROM migration_ledger WHERE version = %s", (version,))
    print(f"rolled back {version} ({path.name})")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--up", action="store_true")
    group.add_argument("--down")
    args = parser.parse_args()
    with connection() as conn:
        with conn.transaction():
            apply_all(conn) if args.up else down_one(conn, args.down)


if __name__ == "__main__":
    main()
