/**
 * content.js – Receiver-side Gmail anti-spoof extension.
 *
 * Intercepts email clicks in the Gmail inbox.  When the user clicks on an
 * email thread:
 *   1. Automatically copies the verification code (stored in the email subject
 *      or body preview) to the clipboard.
 *   2. Shows a verification prompt (WhatsApp or built-in form).
 *   3. Blocks the email from opening until the code has been verified against
 *      the backend /check_verification endpoint.
 *   4. Once verified, unlocks the email and allows normal opening.
 */

(function () {
  "use strict";

  // ── Configuration ────────────────────────────────────────────────────────────
  const BACKEND_URL = "https://YOUR_NGROK_URL"; // Replace with your ngrok URL
  const WHATSAPP_DISPLAY_NUMBER = "+919924024265"; // Displayed to receiver
  const MAX_POLL_ATTEMPTS = 30;
  const POLL_INTERVAL_MS = 2000;

  // Set of thread IDs that have already been verified this session
  const verifiedThreads = new Set();

  // ── Helpers ──────────────────────────────────────────────────────────────────

  /**
   * Extract a verification code (gAAA…== format) from a string.
   */
  function extractCode(text) {
    const match = text.match(/gAAA[A-Za-z0-9+/]+=*/);
    return match ? match[0] : null;
  }

  /**
   * Copy text to the clipboard.
   */
  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback for older environments
      const el = document.createElement("textarea");
      el.value = text;
      el.style.position = "fixed";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      document.body.removeChild(el);
    }
  }

  /**
   * Poll the backend to check whether a code+email has been verified.
   * Resolves to true when verified, false after max attempts.
   */
  async function pollVerification(recipientEmail, code) {
    for (let i = 0; i < MAX_POLL_ATTEMPTS; i++) {
      try {
        const url = new URL(`${BACKEND_URL}/check_verification`);
        url.searchParams.set("email", recipientEmail);
        url.searchParams.set("code", code);
        const res = await fetch(url.toString());
        if (res.ok) {
          const data = await res.json();
          if (data.verified) return true;
        }
      } catch {
        // Network error – keep polling
      }
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    }
    return false;
  }

  // ── Overlay UI ───────────────────────────────────────────────────────────────

  /**
   * Create and show the verification overlay.
   * Returns a Promise that resolves once the user has verified (or cancelled).
   */
  function showVerificationOverlay(code, recipientEmail, threadEl) {
    return new Promise((resolve) => {
      // Backdrop
      const backdrop = document.createElement("div");
      backdrop.style.cssText = `
        position: fixed; inset: 0;
        background: rgba(0,0,0,0.55);
        z-index: 99999;
        display: flex; align-items: center; justify-content: center;
      `;

      // Card — built entirely with safe DOM APIs to prevent XSS
      const card = document.createElement("div");
      card.style.cssText = `
        background: #fff;
        border-radius: 8px;
        box-shadow: 0 4px 24px rgba(0,0,0,0.25);
        padding: 28px 32px;
        max-width: 420px;
        width: 100%;
        font-family: 'Google Sans', Roboto, sans-serif;
      `;

      // Helper to create a styled element with optional text content
      function el(tag, styles, text) {
        const e = document.createElement(tag);
        if (styles) e.style.cssText = styles;
        if (text !== undefined) e.textContent = text;
        return e;
      }

      const heading = el(
        "h2",
        "margin:0 0 8px;font-size:18px;color:#202124;",
        "🔐 Email Verification Required"
      );
      const intro = el(
        "p",
        "margin:0 0 16px;font-size:14px;color:#5f6368;",
        "This email is protected. Please verify the unique code to open it."
      );
      const codeBox = el(
        "div",
        "background:#f8f9fa;border:1px solid #dadce0;border-radius:4px;" +
          "padding:10px 14px;margin-bottom:16px;font-family:monospace;" +
          "font-size:14px;word-break:break-all;color:#202124;",
        code
      );
      const copiedNote = el(
        "p",
        "margin:0 0 4px;font-size:13px;color:#5f6368;",
        "✅ Code copied to clipboard automatically."
      );

      // WhatsApp button
      const waBtn = el(
        "button",
        "display:block;width:100%;margin:12px 0 0;padding:10px;" +
          "background:#25d366;color:#fff;border:none;border-radius:4px;" +
          "font-size:14px;font-weight:600;cursor:pointer;",
        `📱 Send Code to ${WHATSAPP_DISPLAY_NUMBER} via WhatsApp`
      );
      waBtn.id = "gas-whatsapp-btn";

      // Built-in verification form
      const details = document.createElement("details");
      details.style.cssText = "margin-top:12px;";
      const summary = el(
        "summary",
        "cursor:pointer;font-size:13px;color:#1a73e8;",
        "Or verify directly here"
      );
      const formDiv = el("div", "margin-top:10px;");
      const codeInput = el(
        "input",
        "width:100%;box-sizing:border-box;padding:8px;border:1px solid #dadce0;" +
          "border-radius:4px;font-size:13px;"
      );
      codeInput.id = "gas-code-input";
      codeInput.type = "text";
      codeInput.placeholder = "Paste verification code";
      const verifyBtn = el(
        "button",
        "margin-top:8px;padding:8px 16px;background:#1a73e8;color:#fff;" +
          "border:none;border-radius:4px;font-size:13px;cursor:pointer;",
        "Verify"
      );
      verifyBtn.id = "gas-verify-btn";
      const verifyStatus = el(
        "span",
        "display:block;margin-top:6px;font-size:12px;color:#d93025;"
      );
      verifyStatus.id = "gas-verify-status";
      formDiv.append(codeInput, verifyBtn, verifyStatus);
      details.append(summary, formDiv);

      // Cancel button
      const cancelBtn = el(
        "button",
        "display:block;width:100%;margin-top:16px;padding:8px;" +
          "background:#fff;color:#5f6368;border:1px solid #dadce0;" +
          "border-radius:4px;font-size:13px;cursor:pointer;",
        "Cancel"
      );
      cancelBtn.id = "gas-cancel-btn";

      card.append(heading, intro, codeBox, copiedNote, waBtn, details, cancelBtn);

      backdrop.appendChild(card);
      document.body.appendChild(backdrop);

      let polling = false;

      // WhatsApp button
      card.querySelector("#gas-whatsapp-btn").addEventListener("click", async () => {
        const waBtnEl = card.querySelector("#gas-whatsapp-btn");
        if (waBtnEl.dataset.sending) return;
        waBtnEl.dataset.sending = "true";
        waBtnEl.disabled = true;
        waBtnEl.textContent = "⏳ Sending…";

        const statusEl = document.createElement("p");
        statusEl.style.cssText = "font-size:12px;color:#1a73e8;margin:8px 0 0;";
        waBtnEl.after(statusEl);

        try {
          const res = await fetch(`${BACKEND_URL}/send_verification_code`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code, recipient_email: recipientEmail }),
          });

          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `Server error ${res.status}`);
          }

          waBtnEl.textContent = `✅ Code sent to ${WHATSAPP_DISPLAY_NUMBER}`;
          waBtnEl.style.background = "#34a853";
          statusEl.textContent =
            `Code sent to ${WHATSAPP_DISPLAY_NUMBER} ✅ — waiting for WhatsApp confirmation…`;

          // Start polling for backend confirmation
          if (!polling) {
            polling = true;
            pollVerification(recipientEmail, code).then((verified) => {
              if (verified) {
                backdrop.remove();
                resolve(true);
              } else {
                statusEl.textContent =
                  "⏰ Verification timed out. Please enter the code manually below.";
                statusEl.style.color = "#d93025";
                polling = false;
              }
            });
          }
        } catch (err) {
          waBtnEl.textContent = `📱 Send Code to ${WHATSAPP_DISPLAY_NUMBER} via WhatsApp`;
          waBtnEl.disabled = false;
          delete waBtnEl.dataset.sending;
          statusEl.textContent = `❌ Error: ${err.message}`;
          statusEl.style.color = "#d93025";
        }
      });

      // Built-in verify button
      card.querySelector("#gas-verify-btn").addEventListener("click", async () => {
        const inputCode = card.querySelector("#gas-code-input").value.trim();
        const statusEl = card.querySelector("#gas-verify-status");

        if (!inputCode) {
          statusEl.textContent = "Please enter the verification code.";
          return;
        }

        try {
          const url = new URL(`${BACKEND_URL}/check_verification`);
          url.searchParams.set("email", recipientEmail);
          url.searchParams.set("code", inputCode);
          const res = await fetch(url.toString());
          const data = await res.json();

          if (data.verified) {
            backdrop.remove();
            resolve(true);
          } else {
            statusEl.textContent =
              data.message || "Code not verified. Please try again.";
          }
        } catch (err) {
          statusEl.textContent = `Error: ${err.message}`;
        }
      });

      // Cancel button
      card.querySelector("#gas-cancel-btn").addEventListener("click", () => {
        backdrop.remove();
        resolve(false);
      });
    });
  }

  // ── Interception logic ───────────────────────────────────────────────────────

  /**
   * Handle an intercepted email-row click event.
   */
  async function handleEmailClick(event, rowEl) {
    // Determine a stable thread identifier from the row element
    const threadId =
      rowEl.dataset.threadId ||
      rowEl.getAttribute("data-thread-perm-id") ||
      rowEl.id ||
      "";

    // Already verified this session
    if (threadId && verifiedThreads.has(threadId)) return;

    // Extract the verification code from the row's text content (subject/snippet)
    const rowText = rowEl.textContent || "";
    const code = extractCode(rowText);

    // If no code found, allow normal open (email not anti-spoof protected)
    if (!code) return;

    // Prevent the email from opening
    event.stopImmediatePropagation();
    event.preventDefault();

    // Try to get recipient email (current logged-in user)
    const recipientEmail =
      document.querySelector("[data-email]")?.dataset.email || "";

    // Copy code to clipboard automatically
    await copyToClipboard(code);

    // Show verification overlay
    const verified = await showVerificationOverlay(code, recipientEmail, rowEl);

    if (verified) {
      if (threadId) verifiedThreads.add(threadId);
      // Mark row visually
      rowEl.style.outline = "2px solid #34a853";
      // Re-trigger the click so Gmail opens the email normally
      rowEl.click();
    }
  }

  // ── Observer ─────────────────────────────────────────────────────────────────

  /**
   * Attach click listeners to all inbox email rows.
   */
  function attachEmailRowListeners() {
    // Gmail inbox rows have role="row" inside the main table
    const rows = document.querySelectorAll(
      'tr.zA:not([data-gas-listener]), ' +
      '[role="row"]:not([data-gas-listener])'
    );

    rows.forEach((row) => {
      row.dataset.gasListener = "true";
      row.addEventListener("click", (e) => handleEmailClick(e, row), true);
    });
  }

  const observer = new MutationObserver(attachEmailRowListeners);
  observer.observe(document.body, { childList: true, subtree: true });

  // Run once on initial load
  attachEmailRowListeners();
})();
