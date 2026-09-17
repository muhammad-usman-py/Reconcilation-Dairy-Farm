"""Persistent reconciliation history and manual-review storage.

SQLite is deliberately used as the zero-setup first database.  Set
RECONCILIATION_DB_PATH to a managed database file in deployment; the service
layer keeps the Streamlit UI independent from the storage location.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import pickle
import sqlite3

import pandas as pd

from .detailed_reconciliation import DetailedReconciliationResult


DEFAULT_DATABASE_PATH = Path("data/reconciliation_history.db")


def _database_path() -> Path:
    path = Path(os.getenv("RECONCILIATION_DB_PATH", DEFAULT_DATABASE_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialise_history_database() -> None:
    with _connection() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS reconciliation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                first_name TEXT NOT NULL,
                second_name TEXT NOT NULL,
                left_rows INTEGER NOT NULL,
                right_rows INTEGER NOT NULL,
                left_balance REAL NOT NULL,
                right_balance REAL NOT NULL,
                difference REAL NOT NULL,
                report_blob BLOB NOT NULL,
                excel_report BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_items (
                run_id INTEGER NOT NULL,
                ref TEXT NOT NULL,
                status TEXT NOT NULL,
                reviewer TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, ref),
                FOREIGN KEY (run_id) REFERENCES reconciliation_runs(id) ON DELETE CASCADE
            );
        """)


def save_run(
    detailed: DetailedReconciliationResult,
    first_name: str,
    second_name: str,
    excel_report: bytes,
) -> int:
    """Save a completed run and return its immutable history id."""
    initialise_history_database()
    balances = detailed.summary.loc[detailed.summary["Section"].eq("Balances"), "Amount"].tolist()
    left_balance, right_balance = float(balances[0]), float(balances[1])
    payload = pickle.dumps(detailed, protocol=pickle.HIGHEST_PROTOCOL)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connection() as connection:
        cursor = connection.execute(
            """INSERT INTO reconciliation_runs
               (created_at, first_name, second_name, left_rows, right_rows, left_balance, right_balance, difference, report_blob, excel_report)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (created_at, first_name, second_name, len(detailed.left_source), len(detailed.right_source),
             left_balance, right_balance, right_balance - left_balance, payload, excel_report),
        )
        return int(cursor.lastrowid)


def list_runs() -> pd.DataFrame:
    initialise_history_database()
    with _connection() as connection:
        return pd.read_sql_query(
            """SELECT id AS 'Run ID', created_at AS 'Created (UTC)', first_name AS 'First ledger',
                      second_name AS 'Second ledger', left_rows AS 'First rows', right_rows AS 'Second rows',
                      difference AS 'Required adjustment (second - first)'
               FROM reconciliation_runs ORDER BY id DESC""",
            connection,
        )


def load_run(run_id: int) -> dict | None:
    initialise_history_database()
    with _connection() as connection:
        row = connection.execute("SELECT * FROM reconciliation_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {
        "run_id": int(row["id"]),
        "detailed": pickle.loads(row["report_blob"]),
        "first_name": row["first_name"],
        "second_name": row["second_name"],
        "left_rows": int(row["left_rows"]),
        "right_rows": int(row["right_rows"]),
        "detected": [],
        "excel_report": bytes(row["excel_report"]),
    }


def get_reviews(run_id: int) -> pd.DataFrame:
    initialise_history_database()
    with _connection() as connection:
        return pd.read_sql_query(
            "SELECT ref AS Ref, status AS 'Review status', reviewer AS Reviewer, notes AS Notes, updated_at AS 'Last updated (UTC)' FROM review_items WHERE run_id = ?",
            connection,
            params=(run_id,),
        )


def save_review(run_id: int, ref: str, status: str, reviewer: str, notes: str) -> None:
    initialise_history_database()
    with _connection() as connection:
        connection.execute(
            """INSERT INTO review_items (run_id, ref, status, reviewer, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, ref) DO UPDATE SET status=excluded.status, reviewer=excluded.reviewer,
                   notes=excluded.notes, updated_at=excluded.updated_at""",
            (run_id, ref, status, reviewer.strip(), notes.strip(), datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
