const vaultInput = document.querySelector("#vault");
const usernameInput = document.querySelector("#username");
const keyInput = document.querySelector("#key");
const pageLabel = document.querySelector("#page");
const statusLabel = document.querySelector("#status");
const captureButton = document.querySelector("#capture");
const configButton = document.querySelector("#save-config");

let activePage = null;

function status(message, kind = "") {
  statusLabel.textContent = message;
  statusLabel.dataset.kind = kind;
}

function normalizedVault() {
  return vaultInput.value.trim().replace(/\/$/, "");
}

async function saveConfig() {
  const vault = normalizedVault();
  const username = usernameInput.value.trim();
  const apiKey = keyInput.value.trim();
  if (!vault || !username || !apiKey) throw new Error("Vault URL, username, and API key are required.");
  const origin = `${new URL(vault).origin}/*`;
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("Permission to contact this Vault was not granted.");
  await chrome.storage.local.set({ vault, username, apiKey });
}

async function accessToken() {
  const response = await fetch(`${normalizedVault()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: usernameInput.value.trim(),
      api_key: keyInput.value.trim(),
      remember_me: false,
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `PrintStash returned ${response.status}.`);
  }
  const body = await response.json();
  if (typeof body.access_token !== "string") throw new Error("PrintStash did not return an access token.");
  return body.access_token;
}

configButton.addEventListener("click", async () => {
  try { await saveConfig(); status("Settings saved.", "success"); }
  catch (error) { status(error.message, "error"); }
});

captureButton.addEventListener("click", async () => {
  captureButton.disabled = true;
  try {
    await saveConfig();
    if (!activePage?.url || !/^https?:/.test(activePage.url)) throw new Error("Current page is not an HTTP(S) page.");
    const token = await accessToken();
    const response = await fetch(`${normalizedVault()}/api/v1/inbox`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ url: activePage.url, title: activePage.title, source_kind: "browser" }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `PrintStash returned ${response.status}.`);
    }
    status("Saved to Pending Imports.", "success");
  } catch (error) { status(error.message, "error"); }
  finally { captureButton.disabled = false; }
});

Promise.all([
  chrome.storage.local.get(["vault", "username", "apiKey"]),
  chrome.tabs.query({ active: true, currentWindow: true }),
]).then(([config, tabs]) => {
  vaultInput.value = config.vault || "";
  usernameInput.value = config.username || "";
  keyInput.value = config.apiKey || "";
  activePage = tabs[0] || null;
  pageLabel.textContent = activePage?.title || activePage?.url || "No active page";
});
