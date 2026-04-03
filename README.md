# Gmail Anti-Spoof Extension

A comprehensive Chrome extension system that prevents email spoofing through unique code generation and WhatsApp-based verification.

## Architecture

| Component | File | Description |
|-----------|------|-------------|
| Chrome Extension – Sender | `sender.js` | Injects a "Generate Unique Code" button into Gmail compose |
| Chrome Extension – Receiver | `content.js` | Intercepts email clicks and enforces verification before opening |
| Chrome Extension Manifest | `manifest.json` | Extension configuration and permissions |
| Backend API | `app.py` | Flask API with SQLite database |
| DB Initialisation | `init_db.py` | One-time database schema setup |
| Dependencies | `requirements.txt` | Python package requirements |

## Setup

### 1. Backend (Flask API)

```bash
# Install Python dependencies
pip install -r requirements.txt

# Initialise the database (run once)
python init_db.py

# Start the server (development)
python app.py

# Start the server (production – debug off by default)
ALLOWED_ORIGINS=https://YOUR_NGROK_URL python app.py
```

Expose the server via **ngrok** (or any HTTPS tunnel) so the Chrome extension can reach it:

```bash
ngrok http 5000
# Note the https://xxxx.ngrok-free.app URL
```

Set the `ALLOWED_ORIGINS` environment variable to your ngrok URL to enable CORS:

```bash
ALLOWED_ORIGINS=https://xxxx.ngrok-free.app python app.py
```

### 2. Chrome Extension

1. Open Chrome and go to `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked** and select this directory

#### Required configuration

Before loading the extension, update **both** `sender.js` and `content.js` with your backend URL:

```js
// In sender.js (line ~17) and content.js (line ~18):
const BACKEND_URL = "https://xxxx.ngrok-free.app"; // ← your ngrok URL
```

Also update the WhatsApp number in `content.js`:

```js
const WHATSAPP_NUMBER = "1234567890"; // ← E.164 format without +
```

#### Icons

The extension expects icon files at these paths (not included – add your own):

```
icons/icon16.png
icons/icon48.png
icons/icon128.png
```

### 3. WhatsApp Integration (Twilio)

Configure a Twilio WhatsApp sandbox and set the webhook URL to:

```
https://YOUR_NGROK_URL/whatsapp
```

When a recipient replies with the verification code via WhatsApp, the backend automatically marks the code as verified.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate_code` | Generate a unique code for a sender→recipient pair |
| POST | `/store_code_mapping` | Store an externally generated code mapping |
| GET | `/check_verification` | Check if a code+email pair has been verified |
| POST | `/whatsapp` | Twilio WhatsApp webhook for verification confirmations |

## Security Features

- Cryptographically secure code generation (`secrets` module)
- Email validation on all endpoints
- Unique constraint on codes prevents reuse
- Rate limiting: max 5 verification attempts per code
- Timestamp tracking for creation and verification
- CORS restricted to configured origins
- XSS-safe DOM construction in the extension (no `innerHTML` with user data)
