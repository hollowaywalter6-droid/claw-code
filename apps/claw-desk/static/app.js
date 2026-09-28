"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const key = "claw-desk-chat-v1";
  const maxSaved = 60;
  let csrf = "";
  let ownerKey = ""; // deliberately memory-only: never persisted to localStorage
  let cliAvailable = false;
  let ready = false;
  let busy = false;
  let thread = [];

  function load() {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "[]");
      if (Array.isArray(value)) {
        thread = value.filter(x => x && ["user", "assistant"].includes(x.role) && typeof x.content === "string")
          .slice(-maxSaved).map(x => ({role:x.role, content:x.content.slice(0,12000)}));
      }
    } catch (_) { thread = []; }
  }
  function save() {
    try { localStorage.setItem(key, JSON.stringify(thread.slice(-maxSaved))); }
    catch (_) { $("notice").textContent = "Local storage is unavailable; this chat may not persist."; }
  }
  function showConnection(label, kind) {
    const node = $("connection");
    node.className = "connection " + kind;
    node.innerHTML = "<i aria-hidden=\"true\"></i>";
    node.append(document.createTextNode(" " + label));
  }
  function addMessage(role, content) {
    const box = document.createElement("article");
    box.className = "message " + role;
    const title = document.createElement("div");
    title.className = "role";
    title.textContent = role === "user" ? "You" : "Claw";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = content;
    box.append(title, bubble);
    $("messages").append(box);
    $("welcome").hidden = true;
    $("conversation").scrollTop = $("conversation").scrollHeight;
  }
  function redraw() {
    $("messages").replaceChildren();
    $("welcome").hidden = thread.length > 0;
    for (const item of thread) addMessage(item.role, item.content);
  }
  function toggleBusy(next) {
    busy = next;
    $("send").disabled = next || !ready;
    $("prompt").disabled = next || !ready;
    $("mode").disabled = next;
    $("kill-switch").disabled = !ownerKey;
    $("busy").hidden = !next;
  }
  async function request(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (path !== "/api/bootstrap") {
      headers["X-Claw-Desk-Token"] = csrf;
      headers["X-Claw-Desk-Owner-Key"] = ownerKey;
    }
    const response = await fetch(path, {cache:"no-store", credentials:"same-origin", ...options, headers});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof data.error === "string" ? data.error : "Request failed (" + response.status + ")");
    return data;
  }
  async function bootstrap() {
    try {
      const data = await request("/api/bootstrap");
      csrf = data.csrf;
      cliAvailable = Boolean(data.ready);
      ready = false;
      ownerKey = "";
      $("unlock-gate").hidden = false;
      $("unlock-error").textContent = "";
      showConnection("Locked", "error");
      $("notice").textContent = "Enter your owner key to connect securely.";
      $("send").disabled = true;
      $("kill-switch").disabled = true;
      $("prompt").disabled = true;
      $("owner-key").focus();
    } catch (_) {
      ready = false;
      ownerKey = "";
      $("unlock-gate").hidden = true;
      showConnection("Offline", "error");
      $("notice").textContent = "Host unavailable. Check your iPhone Tailscale connection and reopen the app.";
      $("send").disabled = true;
      $("kill-switch").disabled = true;
      $("prompt").disabled = true;
    }
  }
  async function unlock(event) {
    event.preventDefault();
    const key = $("owner-key").value;
    if (!csrf || key.length < 24) return;
    ownerKey = key;
    $("unlock-button").disabled = true;
    $("unlock-error").textContent = "";
    try {
      const data = await request("/api/unlock", {method:"POST"});
      if (!data.authorized) throw Error("Owner key not accepted.");
      $("owner-key").value = ""; // browser and DOM never retain the key
      $("unlock-gate").hidden = true;
      cliAvailable = Boolean(data.ready);
      ready = cliAvailable;
      showConnection(ready ? "Connected" : "CLI missing", ready ? "ready" : "error");
      $("notice").textContent = ready ? "Private host connected. Owner kill switch is available." : "Build the Claw CLI on your host to enable chat.";
      $("send").disabled = !ready;
      $("prompt").disabled = !ready;
      $("kill-switch").disabled = false; // can stop the web host even if CLI binary is missing
      if (ready) $("prompt").focus();
    } catch (error) {
      ownerKey = "";
      $("unlock-error").textContent = error.message;
    } finally {
      $("unlock-button").disabled = false;
    }
  }
  async function shutdown() {
    if (!ownerKey || !csrf) return;
    if (!window.confirm("EMERGENCY STOP\n\nShut down the Claw Desk host and cancel active Claw tasks now? The iPhone app cannot restart it remotely. Continue?")) return;
    $("kill-switch").disabled = true;
    try {
      const result = await request("/api/shutdown", {method:"POST"});
      if (!result.stopped) throw Error("Shutdown was not confirmed.");
      ownerKey = "";
      csrf = "";
      ready = false;
      cliAvailable = false;
      $("unlock-gate").hidden = true;
      $("send").disabled = true;
      $("prompt").disabled = true;
      $("mode").disabled = true;
      $("busy").hidden = true;
      showConnection("Shut down", "error");
      $("notice").textContent = "OWNER SHUTDOWN COMPLETE. The host must be restarted to use Claw again.";
      addMessage("assistant", "Owner emergency shutdown accepted. Claw Desk is stopped and active Claw requests were cancelled. This iPhone view is now disconnected.");
    } catch (error) {
      $("notice").textContent = "Shutdown not confirmed: " + error.message;
      $("kill-switch").disabled = false;
    }
  }
  function historyForRequest() {
    return thread.slice(-8).map(item => ({
      role: item.role, content: item.content.slice(0, 2000)
    }));
  }
  async function send(event) {
    event.preventDefault();
    const text = $("prompt").value.trim();
    if (!ready || busy || !text || text.length > 6000) return;
    const previous = historyForRequest();
    const mode = $("mode").value;
    $("prompt").value = "";
    $("prompt").style.height = "";
    addMessage("user", text);
    toggleBusy(true);
    $("input-hint").textContent = "Working on your request…";
    try {
      const result = await request("/api/chat", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({message:text, mode, history:previous})
      });
      const reply = typeof result.message === "string" && result.message ? result.message : "(Claw returned an empty answer.)";
      addMessage("assistant", reply);
      thread.push({role:"user",content:text},{role:"assistant",content:reply});
      thread = thread.slice(-maxSaved);
      save();
      $("notice").textContent = mode === "workspace-write" ? "Workspace-write mode was used for the last message." : "Read-only request completed.";
    } catch (error) {
      addMessage("assistant", "Unable to complete that message: " + error.message + "\n\nCheck the host connection or run Diagnostics.");
      $("prompt").value = text;
      $("notice").textContent = "Request unsuccessful. Your draft is restored.";
      if (/fetch|network/i.test(error.message)) {
        showConnection("Connection lost", "error");
        ready = false;
      }
    } finally {
      toggleBusy(false);
      $("input-hint").textContent = "Enter to send on desktop · iPhone: tap ↑";
      $("send").disabled = !ready;
      $("prompt").disabled = !ready;
      $("conversation").scrollTop = $("conversation").scrollHeight;
    }
  }
  function clearChat() {
    if (!thread.length) return;
    if (!window.confirm("Clear this conversation from this device? This cannot be undone.")) return;
    thread = [];
    save();
    $("messages").replaceChildren();
    $("welcome").hidden = false;
    $("diagnostics").hidden = true;
    $("notice").textContent = "Conversation cleared from this device.";
    $("prompt").focus();
  }
  async function inspect(path, label) {
    $("diagnostics").hidden = false;
    $("diagnostics-label").textContent = label;
    $("diagnostics-output").textContent = "Loading…";
    $("conversation").scrollTop = $("conversation").scrollHeight;
    try {
      const data = await request(path);
      $("diagnostics-output").textContent = typeof data.message === "string"
        ? data.message + (data.data ? "\n\n" + JSON.stringify(data.data, null, 2) : "")
        : JSON.stringify(data, null, 2);
    } catch (error) {
      $("diagnostics-output").textContent = error.message;
    }
    $("conversation").scrollTop = $("conversation").scrollHeight;
  }
  function menu(open) {
    $("mobile-tools").hidden = !open;
    $("mobile-menu").setAttribute("aria-expanded", String(open));
  }
  $("chat-form").addEventListener("submit", send);
  $("unlock-form").addEventListener("submit", unlock);
  $("kill-switch").addEventListener("click", shutdown);
  $("prompt").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229
        && !/iPhone|iPad|iPod/.test(navigator.userAgent)) {
      event.preventDefault();
      $("chat-form").requestSubmit();
    }
  });
  $("prompt").addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 160) + "px";
  });
  $("mode").addEventListener("change", event => {
    if (event.target.value === "workspace-write" &&
        !window.confirm("Enable workspace-write for upcoming messages? Claw may edit files inside the host repository. Never use this mode for untrusted prompts.")) {
      event.target.value = "read-only";
    }
  });
  for (const btn of document.querySelectorAll(".suggestion")) {
    btn.addEventListener("click", () => {
      $("prompt").value = btn.getAttribute("data-prompt");
      $("prompt").dispatchEvent(new Event("input"));
      $("prompt").focus();
    });
  }
  $("check-status").addEventListener("click", () => inspect("/api/status", "System status"));
  $("run-doctor").addEventListener("click", () => inspect("/api/doctor", "Health check"));
  $("status-mobile").addEventListener("click", () => { menu(false); inspect("/api/status", "System status"); });
  $("doctor-mobile").addEventListener("click", () => { menu(false); inspect("/api/doctor", "Health check"); });
  $("clear-chat-side").addEventListener("click", clearChat);
  $("clear-chat-mobile").addEventListener("click", () => { menu(false); clearChat(); });
  $("close-diagnostics").addEventListener("click", () => { $("diagnostics").hidden = true; });
  $("mobile-menu").addEventListener("click", () => menu($("mobile-tools").hidden));
  window.addEventListener("online", bootstrap);
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  load();
  redraw();
  bootstrap();
})();