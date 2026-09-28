# Claw Desk · iPhone Web App (owner controlled)

Claw Desk is a **mobile-first, installable web companion** for this repo's existing Rust `claw` CLI. It does not pretend to run the Rust CLI or a model natively on an iPhone: a trusted always-on computer or private server runs Claw, and your iPhone accesses its interface using **private Tailscale Serve HTTPS**. This design needs no Apple Developer account, Xcode, or App Store distribution.

## What works

- Standalone iPhone Home Screen experience (PWA), safe-area layout and large tap targets.
- Private owner-key unlock; key stays in page memory and is never saved to browser storage.
- Read-only prompts by default, optional explicitly confirmed workspace-write mode.
- Chat history stored locally in the browser (last 60 entries), bounded context replay (last 8).
- System status and health checks from the real Rust CLI.
- Visible **red ⏻ emergency owner shutdown** in the top bar, including on iPhone. After confirmation the host invalidates the session token, kills active Claw child processes (and their Unix process groups), and stops the HTTP server. No remote restart bypass is built in.
- Only static UI files are cached by the service worker. AI requests and owner credentials are never cached.

## Host setup (Mac, Linux, Windows with Python 3)

On the **trusted host**, not on the iPhone:

```bash
git clone --branch feat/claw-desk-web-app https://github.com/hollowaywalter6-droid/claw-code.git
cd claw-code/rust
cargo build -p rusty-claude-cli
cd ..

# Configure a real provider on the HOST only (example):
export ANTHROPIC_API_KEY="YOUR_ANTHROPIC_API_KEY"

# Optional: supply your own random >=24-character key.
# If omitted, the host prints a generated key once on startup.
export CLAW_DESK_OWNER_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# Local preview, only on the host:
python3 apps/claw-desk/server.py
# Browser: http://127.0.0.1:8765
```

For Windows PowerShell: set provider and owner key with `$env:ANTHROPIC_API_KEY = "..."` and `$env:CLAW_DESK_OWNER_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"`. Build from `rust` as above. No API keys or private credentials belong in GitHub.

If the Claw binary is absent, the interface still loads, but chat is unavailable; build `rusty-claude-cli` first. The host remains responsible for API connectivity and cost.

## Use it on your iPhone from anywhere in your private Tailscale network

1. Install/sign in to Tailscale on the host **and your iPhone**, under your trusted tailnet. Limit Serve access in your tailnet ACLs to your intended devices/users.
2. Find/enable your tailnet's private HTTPS and DNS hostname using Tailscale. It looks like `https://host-name.tailnet-name.ts.net`.
3. Stop any local preview server, then relaunch the server on the trusted host with its exact HTTPS origin:

   ```bash
   export CLAW_DESK_PUBLIC_ORIGIN="https://host-name.tailnet-name.ts.net"
   python3 apps/claw-desk/server.py
   ```

   On PowerShell: `$env:CLAW_DESK_PUBLIC_ORIGIN = "https://host-name.tailnet-name.ts.net"`, then `python apps/claw-desk/server.py`.
4. In a separate host terminal, expose the localhost service privately:

   ```bash
   tailscale serve --bg 8765
   tailscale serve status
   ```

5. On the iPhone, open the private HTTPS URL in Safari, tap **Share → Add to Home Screen**, keep **Open as Web App** enabled, then tap **Add**. Open the Home Screen icon and enter the host's **owner key**.
6. Check Status, then send your first message.

**Do not use `tailscale funnel`, public port forwarding, a public reverse proxy, or expose port 8765 to your LAN.** The server deliberately binds to `127.0.0.1` only and accepts only the explicitly configured private HTTPS *.ts.net origin (plus local loopback). PWA offline mode loads only its visual shell, not chat or host AI features.

## Emergency shutdown (owner kill switch)

Tap the **red power ⏻ button** in the top bar, then explicitly confirm. The request requires the current ephemeral session token and the owner's private key. It sets a server-wide stop flag, invalidates the token, terminates tracked Claw CLI jobs (including the Unix process group where available), and shuts down the web host. Chat immediately stops accepting requests. The current page may remain visible, but it is disconnected.

**Restart:** access the trusted host and run `python3 apps/claw-desk/server.py` again. If Tailscale Serve remains enabled, its URL reconnects when the host restarts. To also remove the reverse-proxy configuration, run `tailscale serve reset` on the host. You cannot use this web app to remotely power off the computer, revoke Tailscale, or restart an already stopped server.

The owner key is intentionally **not a hidden backdoor**, is never included in API responses, and is not stored in the PWA; closing/reloading the page requires unlocking again. Treat the key as a secret. The visible bootstrap nonce alone cannot authorize commands.

## Security and limitations

- Browser-to-host: private HTTPS through Tailscale Serve; loopback HTTP on the host.
- Only known CLI actions are exposed: `prompt`, `status`, `doctor`. No general shell HTTP endpoint or unbounded CLI arguments.
- Read-only is the default. Workspace-write can change files; the UI asks for confirmation before selecting it.
- Chat history uses the iPhone browser's localStorage; use **Clear chat** to remove it. Native Claw CLI session persistence is not represented as a mobile app feature.
- A stopped/offline host cannot be reached from the phone. A service worker may still show cached UI but not fabricate AI responses.
- The kill switch is best effort for subprocess cleanup on Windows and explicitly kills the entire spawned process group on Unix.
- No iPhone App Store package, microphone/transcription, camera upload, native push or remote hosting is claimed in this MVP.

## Tests

```bash
python3 -m unittest discover -s apps/claw-desk/tests -v
python3 -m compileall -q apps/claw-desk
node --check apps/claw-desk/static/app.js
```

The Python suite tests auth, invalid input, private origin, CLI contract and authenticated remote shutdown without a live provider key.
