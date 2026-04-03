/**
 * sender.js – Sender-side Gmail anti-spoof extension.
 *
 * Injects a "Generate Unique Code" button next to Gmail's send button inside
 * every compose window.  When clicked the button:
 *   1. Generates a cryptographically secure unique code (gAAA…== format).
 *   2. Extracts the recipient email from the compose "To" field.
 *   3. Calls the backend to store the sender → recipient → code mapping.
 *   4. Displays the generated code to the sender.
 *   5. Enables the send button (which is disabled until a code is generated).
 */

(function () {
  "use strict";

  // ── Configuration ────────────────────────────────────────────────────────────
  const BACKEND_URL = "https://YOUR_NGROK_URL"; // Replace with your ngrok URL

  // ── Helpers ──────────────────────────────────────────────────────────────────

  /**
   * Generate a cryptographically secure unique code in the format gAAA…==
   * Uses the Web Crypto API available in extension content scripts.
   */
  function generateUniqueCode() {
    const randomBytes = new Uint8Array(16);
    crypto.getRandomValues(randomBytes);
    const base64 = btoa(String.fromCharCode(...randomBytes));
    return "gAAA" + base64;
  }

  /**
   * Extract the sender's logged-in Gmail address from the page.
   */
  function getSenderEmail() {
    const accountEl = document.querySelector(
      'a[href*="SignOutOptions"], [data-email]'
    );
    if (accountEl) {
      return accountEl.dataset.email || accountEl.textContent.trim();
    }
    // Fallback: look for the account switcher title
    const titleEl = document.querySelector(".gb_db.gbii");
    return titleEl ? titleEl.getAttribute("aria-label") : "";
  }

  /**
   * Extract the recipient email address(es) from a compose window.
   * @param {Element} composeEl – The root compose container element.
   */
  function getRecipientEmail(composeEl) {
    // Gmail stores recipient chips in elements with data-hovercard-id
    const chips = composeEl.querySelectorAll("[data-hovercard-id]");
    if (chips.length > 0) {
      return chips[0].dataset.hovercardId;
    }
    // Fallback: look for the "To" input value
    const toInput = composeEl.querySelector('input[name="to"]');
    if (toInput) return toInput.value.trim();

    // Another fallback: aria-label'd "To" field
    const toField = composeEl.querySelector('[aria-label="To"]');
    if (toField) return toField.textContent.trim();

    return "";
  }

  /**
   * Call the backend to store the code mapping.
   */
  async function storeCodeMapping(senderEmail, recipientEmail, code) {
    const response = await fetch(`${BACKEND_URL}/store_code_mapping`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender_email: senderEmail,
        recipient_email: recipientEmail,
        code: code,
      }),
    });
    if (!response.ok) {
      throw new Error(`Backend error: ${response.status}`);
    }
    return response.json();
  }

  // ── Button injection ─────────────────────────────────────────────────────────

  /**
   * Inject the "Generate Unique Code" button into a compose toolbar.
   * @param {Element} sendButton – Gmail's send button element.
   * @param {Element} composeEl  – The root compose container element.
   */
  function injectGenerateButton(sendButton, composeEl) {
    // Avoid duplicate injection
    if (composeEl.querySelector(".gas-generate-btn")) return;

    const btn = document.createElement("button");
    btn.textContent = "🔐 Generate Unique Code";
    btn.className = "gas-generate-btn";
    btn.title = "Generate a unique anti-spoof code before sending";
    btn.style.cssText = `
      margin-left: 8px;
      padding: 6px 12px;
      border: 1px solid #1a73e8;
      border-radius: 4px;
      background: #fff;
      color: #1a73e8;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      vertical-align: middle;
      line-height: 1.2;
    `;

    // Status label shown below the button
    const statusEl = document.createElement("span");
    statusEl.className = "gas-status";
    statusEl.style.cssText = `
      display: block;
      font-size: 11px;
      margin-top: 4px;
      color: #5f6368;
    `;

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "⏳ Generating…";
      statusEl.textContent = "";

      try {
        const recipientEmail = getRecipientEmail(composeEl);
        if (!recipientEmail) {
          statusEl.textContent = "⚠️ No recipient found. Add a recipient first.";
          btn.disabled = false;
          btn.textContent = "🔐 Generate Unique Code";
          return;
        }

        const senderEmail = getSenderEmail();
        const code = generateUniqueCode();

        await storeCodeMapping(senderEmail, recipientEmail, code);

        // Show the code to the sender using safe DOM construction (no innerHTML)
        statusEl.textContent = "";
        const prefix = document.createTextNode("✅ Code generated: ");
        const codeEl = document.createElement("strong");
        codeEl.style.fontFamily = "monospace";
        codeEl.textContent = code;
        const br = document.createElement("br");
        const shareMsg = document.createTextNode(
          `Share this code with ${recipientEmail} via a trusted channel.`
        );
        statusEl.appendChild(prefix);
        statusEl.appendChild(codeEl);
        statusEl.appendChild(br);
        statusEl.appendChild(shareMsg);

        // Enable the send button
        sendButton.removeAttribute("disabled");
        sendButton.style.opacity = "";

        btn.textContent = "✅ Code Generated";
        btn.style.background = "#e6f4ea";
        btn.style.borderColor = "#34a853";
        btn.style.color = "#34a853";
      } catch (err) {
        statusEl.textContent = `❌ Error: ${err.message}`;
        btn.disabled = false;
        btn.textContent = "🔐 Generate Unique Code";
      }
    });

    // Insert the button right after the send button
    sendButton.parentNode.insertBefore(btn, sendButton.nextSibling);

    // Insert status element after the button
    btn.parentNode.insertBefore(statusEl, btn.nextSibling);

    // Disable the send button until a code is generated
    sendButton.setAttribute("disabled", "true");
    sendButton.style.opacity = "0.5";
  }

  // ── Observer ─────────────────────────────────────────────────────────────────

  /**
   * Watch for Gmail compose windows appearing in the DOM and inject our button.
   */
  const observer = new MutationObserver(() => {
    // Gmail's send button selector (adjust if Gmail updates its markup)
    const sendButtons = document.querySelectorAll(
      '[data-tooltip="Send ‪(Ctrl-Enter)‬"], ' +
      '[data-tooltip="Send"], ' +
      '[aria-label="Send ‪(Ctrl-Enter)‬"], ' +
      '[aria-label="Send"]'
    );

    sendButtons.forEach((sendButton) => {
      // Walk up to find the compose container
      const composeEl =
        sendButton.closest(".dw") ||        // compose window
        sendButton.closest(".nH") ||        // another compose wrapper
        sendButton.closest("form") ||
        sendButton.parentElement;

      if (composeEl && !sendButton.dataset.gasInjected) {
        sendButton.dataset.gasInjected = "true";
        injectGenerateButton(sendButton, composeEl);
      }
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });
})();
