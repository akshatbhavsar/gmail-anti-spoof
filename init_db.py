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
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            code            TEXT    NOT NULL UNIQUE,
            sender_email    TEXT    NOT NULL,
            recipient_email TEXT    NOT NULL,
            created_at      TEXT    NOT NULL,
            verified        INTEGER NOT NULL DEFAULT 0,
            verified_at     TEXT,
            attempts        INTEGER NOT NULL DEFAULT 0
        );
        """
    )

    conn.commit()
    conn.close()

    if already_exists:
        print(f"Database '{DATABASE}' already existed – schema verified/updated.")
    else:
        print(f"Database '{DATABASE}' created with schema.")


if __name__ == "__main__":
    init_db()
