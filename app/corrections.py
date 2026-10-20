from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import DB_PATH
from app.models import Correction


class CorrectionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    supplier_name TEXT NOT NULL,
                    supplier_key TEXT NOT NULL,
                    cost_object_type TEXT NOT NULL,
                    cost_object_id TEXT NOT NULL,
                    cost_object_name TEXT NOT NULL,
                    contact_name TEXT,
                    contact_email TEXT,
                    note TEXT,
                    source_invoice TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def save(self, correction: Correction) -> None:
        created = correction.created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO corrections (
                    created_at, supplier_name, supplier_key, cost_object_type,
                    cost_object_id, cost_object_name, contact_name, contact_email,
                    note, source_invoice
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    correction.supplier_name,
                    correction.supplier_key,
                    correction.cost_object_type,
                    correction.cost_object_id,
                    correction.cost_object_name,
                    correction.contact_name,
                    correction.contact_email,
                    correction.note,
                    correction.source_invoice,
                ),
            )

    def latest_for(self, key: str) -> Correction | None:
        if not key:
            return None
        with self._connect() as con:
            row = con.execute(
                """
                SELECT * FROM corrections
                WHERE supplier_key = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (key,),
            ).fetchone()
        return _row(row) if row else None

    def recent(self, limit: int = 8) -> list[Correction]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM corrections ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row(row) for row in rows]


def _row(row: sqlite3.Row) -> Correction:
    return Correction(
        supplier_name=row["supplier_name"],
        supplier_key=row["supplier_key"],
        cost_object_type=row["cost_object_type"],
        cost_object_id=row["cost_object_id"],
        cost_object_name=row["cost_object_name"],
        contact_name=row["contact_name"],
        contact_email=row["contact_email"],
        note=row["note"],
        source_invoice=row["source_invoice"],
        created_at=row["created_at"],
    )
