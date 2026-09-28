# Claw Desk v0.3 — iPhone AI command center

Claw Desk is a **mobile-first Safari Home Screen app** backed by this fork's real Rust `claw` CLI on a trusted host. A separately scaffolded SwiftUI app is under [`ios/ClawDesk/`](../../ios/ClawDesk/). The iPhone app is a remote client; the Rust CLI and cloud models **do not run inside the Safari PWA**.

## Available features

| Feature | Implementation and important limitation |
| --- | --- |
| Chat + recent conversation history | Existing Claw CLI; last 60 role entries in browser localStorage; bounded eight-entry context replay. No false claim of shared native Claw session resume. |
| Voice conversation | Microphone dictation using browser SpeechRecognition where available; otherwise optional audio recording and host-side OpenAI Whisper transcription when `OPENAI_API_KEY` is set. Browser `speechSynthesis` can read replies aloud and each reply has a Listen button. Secure context/microphone consent required. |
| Attach photos, PDFs, text | Up to three per message, each at most 6 MB, retained in host memory for one hour; image/PDF turns use direct Anthropic multimodal Messages API, and **do not have Claw file-edit tools**. This mode needs `ANTHROPIC_API_KEY` on the host and sends selected attachments to that provider. |
| Google Drive | Optional separately authorized Google OAuth web client; name search and provider links (up to 20 recent matches). Read-only Drive scope. |
| Gmail | Read recent inbox subject, sender, date and snippets; create an **unsent draft** only after confirmation. No automatic email-send endpoint. |
| Google Calendar | Read upcoming events and create individual calendar events only after explicit confirmation. |
| Agent Council | Three actual sequential Claw runs: read-only Architect, scoped Executor, read-only Reviewer. The Executor can write only with explicit permission. This is not 35 simultaneous agents and does not falsely claim full test verification. |
| Autonomous scheduled tasks | Host-side SQLite tasks: once/hourly/daily/weekly AI prompts and optional inbox/calendar briefs. Execution remains read-only, while the trusted host is online; results can be inspected on iPhone. No native push or automatic mail send. |
| Owner emergency shutdown | Visible red power button invalidates the token, stops new requests, kills tracked CLI subprocesses/process groups (Unix), then stops the web host. Restart the trusted host manually. |
| Native offline processing | The SwiftUI companion's offline tab runs Apple's on-device Natural Language language and named-entity detection without an internet connection. It is **not** a generative offline LLM. |
| Native iOS build / App Store preparation | XcodeGen SwiftUI source target is provided; you must compile, sign and install via Xcode/TestFlight, provide store assets/privacy details and obtain Apple's review before App Store distribution. |

## 1. Run Claw Desk on the trusted host (not on the iPhone)

The Rust CLI needs an actual provider API credential (a subscription login alone is not necessarily an API credential):

```bash
git clone --branch feat/claw-desk-web-app https://github.com/hollowaywalter6-droid/claw-code.git
cd claw-code/rust
cargo build -p rusty-claude-cli
cd ..

# Keep secrets on this host, never in Github or browser source.
export ANTHROPIC_API_KEY="your-provider-api-key"
# Needed ONLY for the recorded-audio transcription fallback.
export OPENAI_API_KEY="your-openai-api-key"

# Optional: set a long private owner key, otherwise startup prints a generated one.
export CLAW_DESK_OWNER_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 apps/claw-desk/server.py
# Host-only preview: http://127.0.0.1:8765
```

For Windows use PowerShell's `$env:NAME = "value"` and `python apps/claw-desk/server.py`. When building, the executable is `rust/target/debug/claw.exe`. The app is dependency-free on the Python side and requires Python 3.10+.

Model selection for attachment turns can be changed with `CLAW_DESK_MULTIMODAL_MODEL` (default `claude-sonnet-4-6`). The provider you configure must support Anthropic image/PDF input.

## 2. Connect Google ONCE from your trusted host

The Google connectors you may have installed in ChatGPT are **not automatically accessible in this independent Claw app**. The host needs its own Google Cloud OAuth web-client ID and secret.

1. In your Google Cloud project, enable Google Drive API, Gmail API and Calendar API. Configure the OAuth consent screen/testing users and the requested Drive/Gmail/Calendar scopes. Public production use of some Gmail scopes may require Google's independent verification.
2. Create **OAuth Client ID → Web application** and register exactly this redirect URI for a one-time host-side pairing: `http://localhost:8765/api/google/callback`.
3. On your trusted host set:

   ```bash
   export CLAW_DESK_GOOGLE_CLIENT_ID="...apps.googleusercontent.com"
   export CLAW_DESK_GOOGLE_CLIENT_SECRET="your-oauth-web-client-secret"
   python3 apps/claw-desk/server.py
   ```

4. Open **http://localhost:8765** on the trusted host itself, enter the owner key, choose **Integrations → Connect Google** and finish the external Google consent flow. Then tap Refresh connection.
5. OAuth refresh credentials are saved outside the Git repository in `~/.claw-desk/google-oauth.json`, private file permissions on Unix. Treat the trusted host as a sensitive machine. Do not upload its config directory to Github.
6. Stop that preview host and relaunch with the private iPhone Tailscale origin below; the same saved Google token powers the phone's integrations.

The localhost setup is intentional: a third-party `*.ts.net` redirect URI may be rejected by Google Cloud's domain/redirect restrictions, while localhost redirects on the trusted host avoid the phone-loopback problem. Google sign-in in an embedded native WKWebView should be completed through Safari or, preferably, paired on the host first.

**Privacy:** Creating a scheduled Gmail/calendar digest explicitly authorizes retrieval of the necessary metadata/snippets and submission of that context to your configured AI provider. For photo/PDF turns, the selected file bytes are likewise sent to the configured multimodal provider. The UI never embeds provider API keys.

## 3. Use it on iPhone with PRIVATE HTTPS (Safari PWA)

1. Install Tailscale on your trusted host and iPhone, sign into the same controlled tailnet and restrict access with your tailnet ACLs. Do not publicly expose Claw.
2. Identify the trusted host's private HTTPS URL (example: `https://your-device.tailnet-name.ts.net`).
3. On the trusted host, stop the localhost preview and restart Claw Desk:

   ```bash
   export CLAW_DESK_PUBLIC_ORIGIN="https://your-device.tailnet-name.ts.net"
   python3 apps/claw-desk/server.py
   ```

4. In another host terminal:

   ```bash
   tailscale serve --bg 8765
   tailscale serve status
   ```

5. On iPhone open that exact private HTTPS URL in Safari. Use **Share → Add to Home Screen → Open as Web App → Add**. Launch the Home Screen icon and enter the owner key from the host.

Never use `tailscale funnel`, public port-forwarding or a general-purpose public reverse proxy. Port 8765 binds to host loopback only. A Home Screen PWA may show its static interface when offline, but cannot run chat, autonomous tasks or model inference while disconnected from the host. The native SwiftUI target can still run its local Offline Insights tab.

## 4. What the new tabs do

**Assistant:** regular Claw CLI prompts; Attach opens the iPhone photo/file picker (PNG/JPEG/WEBP/PDF/TXT); Speak starts browser dictation or recorded transcription fallback; Voice reply toggles text-to-speech. Voice and attachment permissions are requested by the browser when needed.

**Agent Council:** enter a project task; choose read-only for a full proposal/review, or explicitly authorize workspace-write for the Executor. The UI polls role outputs. A Reviewer inspection does not replace actual unit tests, and the Council does not publish changes to GitHub automatically.

**Integrations:** Google consent, Drive filename search with source links, Gmail inbox snippets, calendar list, explicitly reviewed Gmail drafts and events. The app cannot send emails; open Gmail to inspect and send a draft yourself.

**Scheduled Tasks:** set your start time on the iPhone and choose once/hourly/daily/weekly. The trusted host persists tasks/results in `~/.claw-desk/scheduled-tasks.sqlite3`. Read your results and remove tasks from the mobile tab. The job runner runs only while the host is powered on; there are no guaranteed background iOS push notifications.

**Emergency stop:** red `⏻` → confirm. Cancels tracked child processes and shuts down the web server; the host must be manually restarted. This does not shut down unrelated processes or the whole computer.

## 5. Optional native app source

See [`ios/ClawDesk/README.md`](../../ios/ClawDesk/README.md) for the SwiftUI / XcodeGen source, device installation and independent offline NLP analysis. A signed App Store binary cannot be produced or published without your Apple team/signing and Apple's review. Full on-device *generative* AI still requires integration and profiling of a license-compatible model/tokenizer; its absence is not disguised by the text-analysis screen.

## Verification

```bash
python3 -m unittest discover -s apps/claw-desk/tests -v
python3 -m compileall -q apps/claw-desk
node --check apps/claw-desk/static/app.js
```

Mobile CI also checks the manifest and app icon. The separate iOS workflow compiles the SwiftUI target without signing where an Xcode runner is available. No actual provider keys, Google OAuth session or iPhone App Store review are exercised by these mocked tests.
