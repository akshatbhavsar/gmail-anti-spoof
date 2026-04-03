"""
app.py – Flask backend for the Gmail Anti-Spoof system.

Endpoints:
  POST /generate_code       – Generate a unique code for a sender→recipient pair.
  POST /store_code_mapping  – Store an externally generated code mapping.
  GET  /check_verification  – Check whether a code+email pair is verified.
  POST /whatsapp            – WhatsApp webhook for verification confirmations.
"""

import os
import re
import sqlite3
import secrets
import base64
from datetime import datetime, timezone

from flask import Flask, request, jsonify, g
from flask_cors import CORS

# ── App setup ──────────────────────────────────────────────────────────────────

app = Flask(__name__)

# Allow requests from localhost (development) and ngrok domains.
# In production, set ALLOWED_ORIGINS env var to a comma-separated list of origins.
_raw_origins = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5000,http://127.0.0.1:5000",
)
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

CORS(app, resources={r"/*": {"origins": ALLOWED_ORIGINS}})

DATABASE = "antispoof.db"
MAX_VERIFICATION_ATTEMPTS = 5

# ── Database helpers ───────────────────────────────────────────────────────────


def get_db():
    """Return a per-request SQLite connection (stored on Flask's g object)."""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db


@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    db = get_db()
    db.executescript(
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
    db.commit()


# Initialise the database the first time the app context is pushed
with app.app_context():
    init_db()

# ── Validation helpers ─────────────────────────────────────────────────────────

# Simple, ReDoS-safe email validation: local@domain.tld
# Uses possessive-style construction (no nested quantifiers) to avoid backtracking.
EMAIL_RE = re.compile(r"^[^\s@]{1,254}@[^\s@]{1,253}$")


def valid_email(email: str) -> bool:
    """Return True only for strings that look like valid email addresses."""
    if "@" not in email or len(email) > 320:
        return False
    local, _, domain = email.partition("@")
    # Domain must contain at least one dot and a non-empty TLD
    if "." not in domain:
        return False
    return bool(EMAIL_RE.match(email))


def generate_code() -> str:
    """Generate a cryptographically secure code in the gAAA…== format."""
    raw = secrets.token_bytes(16)
    b64 = base64.b64encode(raw).decode()
    return "gAAA" + b64


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.route("/generate_code", methods=["POST"])
def api_generate_code():
    """
    Generate a unique code and store the sender→recipient mapping.

    Request JSON:
      { "sender_email": "...", "recipient_email": "..." }

    Response JSON:
      { "code": "gAAA...", "message": "Code generated successfully" }
    """
    data = request.get_json(silent=True) or {}
    sender_email = (data.get("sender_email") or "").strip()
    recipient_email = (data.get("recipient_email") or "").strip()

    if not sender_email or not valid_email(sender_email):
        return jsonify({"error": "Invalid or missing sender_email"}), 400
    if not recipient_email or not valid_email(recipient_email):
        return jsonify({"error": "Invalid or missing recipient_email"}), 400

    code = generate_code()
    created_at = datetime.now(timezone.utc).isoformat()

    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO codes (code, sender_email, recipient_email, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (code, sender_email, recipient_email, created_at),
        )
        db.commit()
    except sqlite3.IntegrityError:
        # Extremely unlikely collision – generate a new code and retry once
        code = generate_code()
        db.execute(
            """
            INSERT INTO codes (code, sender_email, recipient_email, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (code, sender_email, recipient_email, created_at),
        )
        db.commit()

    return jsonify({"code": code, "message": "Code generated successfully"}), 201


@app.route("/store_code_mapping", methods=["POST"])
def api_store_code_mapping():
    """
    Store a code mapping provided by the sender extension.

    Request JSON:
      { "sender_email": "...", "recipient_email": "...", "code": "gAAA..." }

    Response JSON:
      { "message": "Code mapping stored successfully" }
    """
    data = request.get_json(silent=True) or {}
    sender_email = (data.get("sender_email") or "").strip()
    recipient_email = (data.get("recipient_email") or "").strip()
    code = (data.get("code") or "").strip()

    if not sender_email or not valid_email(sender_email):
        return jsonify({"error": "Invalid or missing sender_email"}), 400
    if not recipient_email or not valid_email(recipient_email):
        return jsonify({"error": "Invalid or missing recipient_email"}), 400
    if not code:
        return jsonify({"error": "Missing code"}), 400

    created_at = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO codes (code, sender_email, recipient_email, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (code, sender_email, recipient_email, created_at),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Code already exists"}), 409

    return jsonify({"message": "Code mapping stored successfully"}), 201


@app.route("/check_verification", methods=["GET"])
def api_check_verification():
    """
    Check whether a code has been verified for a given recipient email.
    Also increments the attempt counter and enforces a rate limit.

    Query params: email, code

    Response JSON:
      { "verified": true/false, "message": "..." }
    """
    email = (request.args.get("email") or "").strip()
    code = (request.args.get("code") or "").strip()

    if not email or not valid_email(email):
        return jsonify({"error": "Invalid or missing email"}), 400
    if not code:
        return jsonify({"error": "Missing code"}), 400

    db = get_db()
    row = db.execute(
        "SELECT * FROM codes WHERE code = ? AND recipient_email = ?",
        (code, email),
    ).fetchone()

    if row is None:
        return jsonify({"verified": False, "message": "Code not found"}), 404

    if row["verified"]:
        return jsonify({"verified": True, "message": "Code verified"}), 200

    if row["attempts"] >= MAX_VERIFICATION_ATTEMPTS:
        return (
            jsonify(
                {
                    "verified": False,
                    "message": "Too many attempts. This code has been locked.",
                }
            ),
            429,
        )

    # Increment attempt counter only for unverified codes
    db.execute(
        "UPDATE codes SET attempts = attempts + 1 WHERE id = ?",
        (row["id"],),
    )
    db.commit()

    return jsonify({"verified": False, "message": "Code not yet verified"}), 200


@app.route("/whatsapp", methods=["POST"])
def api_whatsapp():
    """
    WhatsApp webhook endpoint.

    Twilio sends incoming WhatsApp messages as form-encoded data.
    The message body should contain the verification code (gAAA…).
    When a matching code is found it is marked as verified.

    Request form fields (Twilio format):
      Body – message body text
      From – sender's WhatsApp number (e.g. whatsapp:+1234567890)

    Response: TwiML empty response (200 OK).
    """
    body = (request.form.get("Body") or "").strip()
    from_number = (request.form.get("From") or "").strip()

    # Extract code from message body
    match = re.search(r"gAAA[A-Za-z0-9+/]+=*", body)
    if not match:
        # No recognisable code – silently accept (don't break Twilio flow)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response></Response>"
        ), 200

    code = match.group(0)
    verified_at = datetime.now(timezone.utc).isoformat()
    db = get_db()

    result = db.execute(
        "UPDATE codes SET verified = 1, verified_at = ? WHERE code = ? AND verified = 0",
        (verified_at, code),
    )
    db.commit()

    if result.rowcount > 0:
        app.logger.info("Code %s verified via WhatsApp from %s", code, from_number)
    else:
        app.logger.warning(
            "WhatsApp verification attempted for unknown/already-verified code %s", code
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response></Response>"
    ), 200


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)
