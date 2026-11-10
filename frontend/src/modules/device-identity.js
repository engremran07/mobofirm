// Operating-system device registration through a platform WebAuthn credential.
//
// A platform authenticator (Windows Hello/TPM, macOS or Android keystore) is shared by
// every browser on the OS user profile and is destroyed by an OS reinstall, so it
// identifies the computer, not the browser. The server issues the challenge, verifies
// the response and owns every decision; this module only drives the browser APIs.
// Without a platform authenticator, a secure context or JavaScript, the section stays
// hidden and the per-browser fallback form remains the only path.

const SECTION = "[data-device-os]";

function encode(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decode(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0)).buffer;
}

function descriptors(list) {
  return (list || []).map((item) => ({ ...item, id: decode(item.id) }));
}

function setBusy(root, busy) {
  root.setAttribute("aria-busy", busy ? "true" : "false");
  for (const button of root.querySelectorAll("[data-device-os-action]")) {
    button.disabled = busy;
  }
}

function setStatus(root, text) {
  const status = root.querySelector("[data-device-os-status]");
  if (!status) return;
  status.textContent = text;
  status.hidden = !text;
}

function showActions(root, mode) {
  root.dataset.mode = mode;
  const verify = root.querySelector('[data-device-os-action="verify"]');
  const register = root.querySelector('[data-device-os-action="register"]');
  if (verify) verify.hidden = mode !== "verify";
  if (register) {
    register.hidden = false;
    register.classList.toggle("btn-primary", mode !== "verify");
    register.classList.toggle("btn-outline-primary", mode === "verify");
  }
}

// When the computer credential is usable it is the recommended choice: the
// browser-only submit of the surrounding dialog becomes the secondary action.
function prioritiseComputerAction(root) {
  const dialog = root.closest("dialog");
  const browserOnly = dialog?.querySelector('button[name="decision"][value="approve"]');
  if (browserOnly) {
    browserOnly.classList.remove("btn-primary");
    browserOnly.classList.add("btn-outline-primary");
  }
  const primary = root.querySelector(".btn-primary:not([hidden])");
  if (primary instanceof HTMLElement && dialog?.open) primary.focus({ preventScroll: true });
}

function csrfToken(root) {
  return root.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
}

async function post(root, url, body) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRFToken": csrfToken(root),
    },
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  return { ok: response.ok && data.ok === true, data };
}

function explain(error, purpose) {
  switch (error?.name) {
    case "NotAllowedError":
      return purpose === "verify"
        ? "No registered credential for your account was found on this computer, or the request was cancelled. If this is a different computer, register it."
        : "The request was cancelled or timed out. Try again when you are ready.";
    case "InvalidStateError":
      return "This computer already has a registered credential for your account. Choose “Verify this computer”.";
    case "NotSupportedError":
    case "SecurityError":
      return "This browser cannot use the computer credential on this address. Use “Register this browser only” instead.";
    default:
      return "The computer credential could not be used. Use “Register this browser only” instead.";
  }
}

async function options(root, purpose) {
  const result = await post(root, root.dataset.optionsUrl, { purpose });
  if (!result.ok) throw Object.assign(new Error(result.data.message || "options"), { server: result.data });
  return result.data.publicKey;
}

async function register(root) {
  const publicKey = await options(root, "register");
  const credential = await navigator.credentials.create({
    publicKey: {
      ...publicKey,
      challenge: decode(publicKey.challenge),
      user: { ...publicKey.user, id: decode(publicKey.user.id) },
      excludeCredentials: descriptors(publicKey.excludeCredentials),
    },
  });
  const response = credential?.response;
  if (
    !response ||
    typeof response.getPublicKey !== "function" ||
    typeof response.getAuthenticatorData !== "function" ||
    typeof response.getPublicKeyAlgorithm !== "function" ||
    !response.getPublicKey()
  ) {
    throw Object.assign(new Error("unsupported"), { name: "NotSupportedError" });
  }
  return post(root, root.dataset.registerUrl, {
    credentialId: encode(credential.rawId),
    clientDataJSON: encode(response.clientDataJSON),
    authenticatorData: encode(response.getAuthenticatorData()),
    publicKey: encode(response.getPublicKey()),
    publicKeyAlgorithm: response.getPublicKeyAlgorithm(),
  });
}

async function verify(root) {
  const publicKey = await options(root, "verify");
  const credential = await navigator.credentials.get({
    publicKey: {
      ...publicKey,
      challenge: decode(publicKey.challenge),
      allowCredentials: descriptors(publicKey.allowCredentials),
    },
  });
  const response = credential.response;
  return post(root, root.dataset.verifyUrl, {
    credentialId: encode(credential.rawId),
    clientDataJSON: encode(response.clientDataJSON),
    authenticatorData: encode(response.authenticatorData),
    signature: encode(response.signature),
  });
}

async function run(root, purpose) {
  setBusy(root, true);
  setStatus(root, "");
  try {
    const result = await (purpose === "verify" ? verify(root) : register(root));
    if (result.ok) {
      setStatus(root, result.data.message || "Done.");
      // The server queued a flash message; a plain GET shows it and drops the prompt.
      window.location.reload();
      return;
    }
    setStatus(root, result.data.message || "This computer could not be registered. Try again.");
  } catch (error) {
    if (error?.server?.error === "no_credentials") showActions(root, "register");
    setStatus(root, error?.server?.message || explain(error, purpose));
  }
  setBusy(root, false);
}

export async function initializeDeviceIdentity() {
  const sections = document.querySelectorAll(SECTION);
  if (!sections.length) return;
  if (
    !window.isSecureContext ||
    !window.PublicKeyCredential ||
    typeof PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable !== "function" ||
    !navigator.credentials
  ) {
    return;
  }
  let available = false;
  try {
    available = await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    available = false;
  }
  if (!available) return;
  for (const root of sections) {
    root.hidden = false;
    showActions(root, root.dataset.mode === "verify" ? "verify" : "register");
    prioritiseComputerAction(root);
    root.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-device-os-action]") : null;
      if (button instanceof HTMLButtonElement && root.contains(button)) {
        void run(root, button.dataset.deviceOsAction);
      }
    });
  }
}
