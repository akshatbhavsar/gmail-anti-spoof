"""
init_db.py – Database initialisation script.

Run this script once before starting the Flask application to create the
SQLite database and the required tables.

Usage:
    python init_db.py
"""

import sqlite3
import os

DATABASE = "antispoof.db"


def init_db():
    already_exists = os.path.exists(DATABASE)
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS codes (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            code                  TEXT    NOT NULL UNIQUE,
            sender_email          TEXT    NOT NULL,
            recipient_email       TEXT    NOT NULL,
            created_at            TEXT    NOT NULL,
            verified              INTEGER NOT NULL DEFAULT 0,
            verified_at           TEXT,
            attempts              INTEGER NOT NULL DEFAULT 0,
            whatsapp_sent_at      TEXT,
            whatsapp_delivered_at TEXT,
            whatsapp_response_at  TEXT
        );
        """
    )

    # Migrate existing databases: add WhatsApp tracking columns if absent.
    _whatsapp_columns = frozenset(
        {"whatsapp_sent_at", "whatsapp_delivered_at", "whatsapp_response_at"}
    )
    for column in ("whatsapp_sent_at", "whatsapp_delivered_at", "whatsapp_response_at"):
        if column not in _whatsapp_columns:
            continue  # Guard: only allow known column names
        try:
            cursor.execute(f"ALTER TABLE codes ADD COLUMN {column} TEXT")  # noqa: S608 – name is whitelisted
        except Exception:
            pass  # Column already exists – safe to ignore

    conn.commit()
    conn.close()

    if already_exists:
        print(f"Database '{DATABASE}' already existed – schema verified/updated.")
    else:
        print(f"Database '{DATABASE}' created with schema.")


if __name__ == "__main__":
    init_db()
