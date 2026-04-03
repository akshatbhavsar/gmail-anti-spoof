"""
app.py – Flask backend for the Gmail Anti-Spoof system.

Endpoints:
  POST /generate_code            – Generate a unique code for a sender→recipient pair.
  POST /store_code_mapping       – Store an externally generated code mapping.
  GET  /check_verification       – Check whether a code+email pair is verified.
  POST /send_verification_code   – Send a verification code to the receiver via
                                   Fast2SMS WhatsApp Business API.
  POST /receive_whatsapp_webhook – Receive delivery/response callbacks from Fast2SMS
                                   and mark codes as verified.
"""

import os
import re
import sqlite3
import secrets
import base64
from datetime import datetime, timezone

import requests
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

# ── Fast2SMS configuration ─────────────────────────────────────────────────────

FAST2SMS_API_KEY = os.environ.get("FAST2SMS_API_KEY", "")
FAST2SMS_WHATSAPP_URL = os.environ.get(
    "FAST2SMS_WHATSAPP_URL", "https://www.fast2sms.com/dev/whatsapp"
)
# WhatsApp number that receives the verification code
WHATSAPP_RECIPIENT = os.environ.get("WHATSAPP_RECIPIENT", "9924024265")
# Fast2SMS template/message identifiers
FAST2SMS_MESSAGE_ID = os.environ.get("FAST2SMS_MESSAGE_ID", "16753")
FAST2SMS_TEMPLATE_NAME = os.environ.get("FAST2SMS_TEMPLATE_NAME", "project")

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
    """Create tables and columns if they don't exist."""
    db = get_db()
    db.executescript(
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
            db.execute(f"ALTER TABLE codes ADD COLUMN {column} TEXT")  # noqa: S608 – name is whitelisted
        except Exception:
            pass  # Column already exists – safe to ignore
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


@app.route("/send_verification_code", methods=["POST"])
def api_send_verification_code():
    """
    Send the verification code to the receiver's WhatsApp via Fast2SMS.

    Request JSON:
      { "code": "gAAA...", "recipient_email": "..." }

    Response JSON:
      { "message": "Code sent via WhatsApp", "whatsapp_number": "+919924024265" }
    """
    if not FAST2SMS_API_KEY:
        return jsonify({"error": "Fast2SMS API key not configured"}), 503

    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    recipient_email = (data.get("recipient_email") or "").strip()

    if not code:
        return jsonify({"error": "Missing code"}), 400
    if recipient_email and not valid_email(recipient_email):
        return jsonify({"error": "Invalid recipient_email"}), 400

    db = get_db()

    # Look up the code to confirm it exists
    query = "SELECT * FROM codes WHERE code = ?"
    params: tuple = (code,)
    if recipient_email:
        query += " AND recipient_email = ?"
        params = (code, recipient_email)
    row = db.execute(query, params).fetchone()

    if row is None:
        return jsonify({"error": "Code not found"}), 404

    try:
        response = requests.post(
            FAST2SMS_WHATSAPP_URL,
            headers={"Authorization": FAST2SMS_API_KEY},
            json={
                "message_id": FAST2SMS_MESSAGE_ID,
                "phone": WHATSAPP_RECIPIENT,
                "template_variables": [code],
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        app.logger.error("Fast2SMS API error: %s", exc)
        return jsonify({"error": "Failed to send WhatsApp message"}), 502

    sent_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE codes SET whatsapp_sent_at = ? WHERE id = ?",
        (sent_at, row["id"]),
    )
    db.commit()

    app.logger.info(
        "Verification code sent via Fast2SMS WhatsApp to %s for code %s",
        WHATSAPP_RECIPIENT,
        code,
    )
    return jsonify({
        "message": "Code sent via WhatsApp",
        "whatsapp_number": f"+91{WHATSAPP_RECIPIENT}",
    }), 200


@app.route("/receive_whatsapp_webhook", methods=["POST"])
def api_receive_whatsapp_webhook():
    """
    Fast2SMS WhatsApp webhook endpoint.

    Fast2SMS delivers and response callbacks as JSON.  Accepts both delivery
    status updates (to record `whatsapp_delivered_at`) and user reply messages
    (to extract the code and mark it as verified).

    Expected JSON fields (Fast2SMS format):
      message  – message body text (present when the user replies)
      phone    – sender's phone number
      status   – delivery status (e.g. "delivered")
    """
    payload = request.get_json(silent=True) or request.form.to_dict()

    body = (payload.get("message") or payload.get("Body") or "").strip()
    phone = (payload.get("phone") or payload.get("From") or "").strip()
    status = (payload.get("status") or "").strip().lower()

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()

    # Handle delivery status update
    if status == "delivered" and not body:
        # Fast2SMS notifies delivery. Without a per-message ID in the callback
        # we target the single most-recently-sent code that is still awaiting
        # delivery confirmation, to minimise the chance of updating the wrong row.
        db.execute(
            """
            UPDATE codes
            SET    whatsapp_delivered_at = ?
            WHERE  id = (
                SELECT id FROM codes
                WHERE  whatsapp_sent_at IS NOT NULL
                  AND  whatsapp_delivered_at IS NULL
                ORDER  BY whatsapp_sent_at DESC
                LIMIT  1
            )
            """,
            (now,),
        )
        db.commit()
        return jsonify({"status": "ok"}), 200

    # Handle user reply: extract verification code from message body
    match = re.search(r"gAAA[A-Za-z0-9+/]+=*", body)
    if not match:
        return jsonify({"status": "ok"}), 200

    code = match.group(0)
    result = db.execute(
        "UPDATE codes SET verified = 1, verified_at = ?, whatsapp_response_at = ? "
        "WHERE code = ? AND verified = 0",
        (now, now, code),
    )
    db.commit()

    if result.rowcount > 0:
        app.logger.info(
            "Code %s verified via Fast2SMS WhatsApp reply from %s", code, phone
        )
    else:
        app.logger.warning(
            "Fast2SMS webhook: unknown or already-verified code %s from %s",
            code,
            phone,
        )

    return jsonify({"status": "ok"}), 200


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)
