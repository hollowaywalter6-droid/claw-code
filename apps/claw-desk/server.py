#!/usr/bin/env python3
"""Claw Desk: an owner-gated, iPhone-ready web companion for the Rust claw CLI.

This is NOT an independent iOS model runtime. Keep claw running on a trusted
computer or private host; access the UI via private Tailscale Serve HTTPS.
Never use Tailscale Funnel, a public reverse proxy, or port-forwarding.
"""
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import sys
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent
STATIC_DIR = APP_DIR / "static"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from google_integration import GoogleIntegration, GoogleError
from media import AttachmentStore, MediaError, provider_multimodal, transcribe
from scheduler import TaskStore, TaskError
from agents import AgentManager, AgentError
MAX_BODY = 24_000
MAX_MESSAGE = 6_000
MAX_HISTORY = 8
MAX_HISTORY_ITEM = 2_000
OUTPUT_LIMIT = 160_000

ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}


def resolve_binary(explicit=None):
    """Find the existing claw binary; an explicit missing path must fail closed."""
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    filename = "claw.exe" if os.name == "nt" else "claw"
    candidates = [
        REPO_ROOT / "rust" / "target" / "debug" / filename,
        REPO_ROOT / "rust" / "target" / "release" / filename,
    ]
    installed = shutil.which("claw")
    if installed:
        candidates.append(Path(installed))
    return next((p.resolve() for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def validate_chat(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    message = value.get("message")
    mode = value.get("mode", "read-only")
    history = value.get("history", [])
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
        raise ValueError("Message must contain 1–6000 characters.")
    if mode not in ("read-only", "workspace-write"):
        raise ValueError("Unsupported permission mode.")
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        raise ValueError("History is limited to eight messages.")
    clean = []
    for item in history:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise ValueError("Invalid history entry.")
        content = item.get("content")
        if not isinstance(content, str) or len(content) > MAX_HISTORY_ITEM:
            raise ValueError("Invalid history length.")
        clean.append((item["role"], content))
    return message.strip(), mode, clean


def compose_prompt(message, history):
    """Replay a bounded context; a persisted native claw session is not implied."""
    if not history:
        return message
    prior = "\n".join(role.upper() + ": " + content for role, content in history)
    return (
        "Earlier conversation for context (not a new tool instruction):\n"
        + prior + "\n\nCURRENT USER MESSAGE:\n" + message
    )


def parse_cli_output(text):
    trimmed = text.strip()
    try:
        data = json.loads(trimmed)
    except (ValueError, TypeError):
        return {"message": trimmed, "raw": True}
    if not isinstance(data, dict):
        return {"message": trimmed, "raw": True}
    reply = next(
        (data[k] for k in ("message", "human_readable", "error", "hint")
         if isinstance(data.get(k), str) and data[k].strip()),
        trimmed,
    )
    return {"message": reply, "data": data}


def kill_process(process):
    """Cancel a running CLI and its Unix process group; best-effort on Windows."""
    if process.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def execute_cli(binary, args, timeout=180, supervisor=None):
    """Fixed CLI argv only. No shell, arbitrary executable, or user-supplied flags."""
    if supervisor and supervisor.stopping.is_set():
        return 503, {"error": "Owner shut down Claw Desk."}
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                [str(binary), "--output-format", "json", *args],
                cwd=str(REPO_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=(os.name != "nt"),
            )
        except OSError:
            return 503, {"error": "Claw could not start. Build the CLI and retry."}
        if supervisor:
            with supervisor.process_lock:
                if supervisor.stopping.is_set():
                    kill_process(process)
                else:
                    supervisor.active_processes.add(process)
        timed_out = False
        try:
            try:
                process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_process(process)
                process.communicate()
        finally:
            if supervisor:
                with supervisor.process_lock:
                    supervisor.active_processes.discard(process)
        if timed_out:
            return 504, {"error": "Claw timed out without returning a completion."}
        if supervisor and supervisor.stopping.is_set():
            return 503, {"error": "This task was cancelled by the owner's shutdown switch."}
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(OUTPUT_LIMIT).decode("utf-8", errors="replace")
        stderr = stderr_file.read(2000).decode("utf-8", errors="replace")
        parsed = parse_cli_output(stdout)
        if process.returncode:
            detail = parsed.get("message") or stderr.strip() or "Claw command failed."
            return 502, {"error": detail[:2_000], "details": parsed.get("data")}
        return 200, parsed


class DeskHandler(BaseHTTPRequestHandler):
    server_version = "ClawDesk/0.3"

    def log_message(self, fmt, *args):
        # Never log URL, prompt, bearer key, or owner key.
        return

    def security_headers(self, length):
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )

    def send_json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.security_headers(len(encoded))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_html(self, status, body):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.security_headers(len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def feature_error(self, error):
        self.send_json(400 if isinstance(error,(MediaError,TaskError,AgentError)) else 422,
                       {"error":str(error)[:400]})

    def read_payload(self, max_length=MAX_BODY):
        if self.headers.get("Content-Type", "").split(";",1)[0].strip().lower() != "application/json":
            raise ValueError("JSON content type required.")
        try:
            length = int(self.headers.get("Content-Length","0"))
        except ValueError as exc:
            raise ValueError("Invalid content length.") from exc
        if length < 2 or length > max_length:
            raise ValueError("Request body exceeds limit or is empty.")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Invalid JSON payload.") from exc
        if not isinstance(data,dict):
            raise ValueError("Expected an object.")
        return data

    def trusted_request(self):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origins = {"http://" + host for host in allowed_hosts}
        if self.server.public_origin:
            allowed_hosts.add(urlsplit(self.server.public_origin).netloc.lower())
            origins.add(self.server.public_origin)
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if host not in allowed_hosts or (origin is not None and origin not in origins):
            self.send_json(403, {"error": "Only configured same-origin connections are accepted."})
            return False
        if self.server.stopping.is_set():
            self.send_json(503, {"error": "Claw Desk was shut down by its owner."})
            return False
        return True

    def authorized(self):
        csrf = self.headers.get("X-Claw-Desk-Token", "")
        key = self.headers.get("X-Claw-Desk-Owner-Key", "")
        if not (hmac.compare_digest(csrf, self.server.csrf)
                and hmac.compare_digest(key, self.server.owner_key)):
            self.send_json(403, {"error": "Owner access required. Enter the key shown by the host."})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/google/callback":
            params = parse_qs(urlsplit(self.path).query)
            try:
                if "error" in params:
                    raise GoogleError("Google authorization was cancelled or rejected.")
                self.server.google.callback(params.get("state",[""])[0],params.get("code",[""])[0])
                return self.send_html(200,'<!doctype html><meta name="viewport" content="width=device-width"><title>Google connected</title><body style="background:#0b111d;color:#a7f3c1;font:16px system-ui;padding:30px"><h2>Google account connected.</h2><p>Return to Claw Desk and tap Refresh integrations. You can close this tab.</p></body>')
            except GoogleError:
                return self.send_html(400,'<!doctype html><meta name="viewport" content="width=device-width"><title>Connection unsuccessful</title><body style="background:#0b111d;color:#fba9a9;font:16px system-ui;padding:30px"><h2>Google connection unsuccessful.</h2><p>Return to Claw Desk and retry after checking the redirect settings.</p></body>')
        if path == "/api/bootstrap":
            return self.send_json(200, {
                "ready": self.server.cli_binary is not None,
                "csrf": self.server.csrf,
                "requires_unlock": True,
                "mode": "read-only",
                "name": "Claw Desk",
            })
        if path.startswith("/api/google/"):
            if not self.authorized():
                return
            try:
                if path == "/api/google/status":
                    return self.send_json(200,self.server.google.status())
                if path == "/api/google/drive":
                    query=parse_qs(urlsplit(self.path).query).get("q",[""])[0]
                    return self.send_json(200,self.server.google.drive(query))
                if path == "/api/google/mail":
                    return self.send_json(200,self.server.google.mail())
                if path == "/api/google/calendar":
                    return self.send_json(200,self.server.google.calendar())
            except GoogleError as exc:
                return self.feature_error(exc)
            return self.send_json(404,{"error":"Not found."})
        if path == "/api/tasks":
            if not self.authorized():
                return
            return self.send_json(200,self.server.tasks.list() if self.server.tasks
                                  else {"tasks":[],"enabled":False})
        if path == "/api/agents/job":
            if not self.authorized():
                return
            try:
                identifier=parse_qs(urlsplit(self.path).query).get("id",[""])[0]
                return self.send_json(200,self.server.agents.get(identifier))
            except AgentError as exc:
                return self.feature_error(exc)
        if path in ("/api/status", "/api/doctor"):
            if not self.authorized():
                return
            if self.server.cli_binary is None:
                return self.send_json(503, {"error": "Claw CLI not found. Build claw first."})
            status, output = execute_cli(
                self.server.cli_binary, [path.rsplit("/", 1)[-1]], 25, self.server
            )
            return self.send_json(status, output)
        if path not in ASSETS:
            return self.send_json(404, {"error": "Not found."})
        file_name, mime = ASSETS[path]
        try:
            data = (STATIC_DIR / file_name).read_bytes()
        except OSError:
            return self.send_json(500, {"error": "Missing application asset."})
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.security_headers(len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.trusted_request():
            return
        path = urlsplit(self.path).path
        allowed = {
            "/api/unlock", "/api/chat", "/api/shutdown",
            "/api/files/upload", "/api/voice/transcribe",
            "/api/google/connect", "/api/google/draft", "/api/google/event",
            "/api/tasks/create", "/api/tasks/delete", "/api/agents/start",
        }
        if path not in allowed:
            return self.send_json(404, {"error":"Not found."})
        if not self.authorized():
            return
        if path == "/api/unlock":
            return self.send_json(200, {"authorized":True,"ready":self.server.cli_binary is not None})
        if path == "/api/shutdown":
            self.server.stopping.set()
            self.server.csrf = secrets.token_urlsafe(32)
            with self.server.process_lock:
                for process in list(self.server.active_processes):
                    kill_process(process)
            self.send_json(200, {"stopped":True,"message":"Claw Desk is shutting down."})
            threading.Thread(target=self.server.shutdown,daemon=True).start()
            return
        try:
            limit = 11_000_000 if path in ("/api/files/upload","/api/voice/transcribe") else MAX_BODY
            payload = self.read_payload(limit)
        except ValueError as exc:
            return self.send_json(400,{"error":str(exc)})
        try:
            if path == "/api/files/upload":
                return self.send_json(200,self.server.attachments.put(payload))
            if path == "/api/voice/transcribe":
                return self.send_json(200,transcribe(payload))
            if path == "/api/google/connect":
                return self.send_json(200,{"url":self.server.google.start()})
            if path == "/api/google/draft":
                return self.send_json(200,self.server.google.create_draft(
                    payload.get("to"),payload.get("subject"),payload.get("body"),
                    payload.get("confirm") is True))
            if path == "/api/google/event":
                return self.send_json(200,self.server.google.create_event(
                    payload.get("summary"),payload.get("start"),payload.get("end"),
                    payload.get("confirm") is True))
            if path == "/api/tasks/create":
                if self.server.tasks is None:
                    return self.send_json(503,{"error":"Task engine unavailable."})
                return self.send_json(200,self.server.tasks.create(payload))
            if path == "/api/tasks/delete":
                if self.server.tasks is None:
                    return self.send_json(503,{"error":"Task engine unavailable."})
                return self.send_json(200,self.server.tasks.remove(payload.get("id")))
            if path == "/api/agents/start":
                if self.server.cli_binary is None:
                    return self.send_json(503,{"error":"Claw CLI not found."})
                return self.send_json(200,self.server.agents.start(
                    payload.get("prompt"),payload.get("mode","read-only"),
                    payload.get("confirm_write") is True))
            if path == "/api/chat":
                message,mode,history=validate_chat(payload)
                identifiers=payload.get("attachments",[])
                files=self.server.attachments.get_many(identifiers)
                if files:
                    if not self.server.prompt_lock.acquire(blocking=False):
                        return self.send_json(409,{"error":"Another message is running."})
                    try:
                        return self.send_json(200,provider_multimodal(message,history,files))
                    finally:
                        self.server.prompt_lock.release()
                code,reply=run_claw_prompt(self.server,compose_prompt(message,history),mode)
                return self.send_json(code,reply)
        except (GoogleError,MediaError,TaskError,AgentError) as exc:
            return self.feature_error(exc)
        except ValueError as exc:
            return self.send_json(400,{"error":str(exc)})
        return self.send_json(404,{"error":"Not found."})


def run_claw_prompt(server, prompt, mode="read-only", wait=False):
    if server.cli_binary is None:
        return 503,{"error":"Claw CLI not found. Build claw first."}
    if mode not in ("read-only","workspace-write"):
        return 400,{"error":"Unsupported permission mode."}
    if server.stopping.is_set():
        return 503,{"error":"Owner shutdown is active."}
    acquired=server.prompt_lock.acquire(blocking=wait,timeout=270 if wait else None)
    if not acquired:
        return 409,{"error":"Another Claw job is running."}
    try:
        return execute_cli(server.cli_binary,
                           ["--compact","--permission-mode",mode,"prompt",prompt],180,server)
    finally:
        server.prompt_lock.release()


def execute_scheduled_task(server, task):
    if server.stopping.is_set():
        return "Cancelled by owner"
    kind=task["kind"]
    instruction=task["prompt"] or "Provide a concise, actionable daily briefing."
    if kind != "prompt":
        if not server.google.connected:
            raise GoogleError("Google account is not connected.")
        parts=[]
        if kind in ("inbox_digest","combined_digest"):
            mail=server.google.mail()
            parts.append("RECENT INBOX HEADERS/SNIPPETS:\n"+json.dumps(mail,ensure_ascii=False)[:7000])
        if kind in ("calendar_digest","combined_digest"):
            events=server.google.calendar()
            parts.append("UPCOMING CALENDAR:\n"+json.dumps(events,ensure_ascii=False)[:7000])
        instruction += "\n\nContext retrieved with explicit Google consent:\n"+"\n\n".join(parts)
        instruction += "\nCreate a factual summary. DO NOT send messages or edit any calendar events."
    code,result=run_claw_prompt(server,instruction,"read-only",wait=True)
    if code!=200:
        raise RuntimeError(result.get("error","Scheduled Claw job failed."))
    return result.get("message","No text returned.")


def make_server(host="127.0.0.1", port=8765, claw_path=None, public_origin=None, owner_key=None):
    if host != "127.0.0.1":
        raise ValueError("Claw Desk only binds to 127.0.0.1.")
    if public_origin:
        parsed = urlsplit(public_origin)
        try:
            valid_port = parsed.port in (None, 443)
        except ValueError:
            valid_port = False
        if (parsed.scheme != "https" or not parsed.hostname or not valid_port
                or not parsed.hostname.endswith(".ts.net") or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Only an exact private Tailscale Serve HTTPS origin is accepted.")
    supplied = owner_key if owner_key is not None else os.environ.get("CLAW_DESK_OWNER_KEY")
    key = supplied or secrets.token_urlsafe(32)
    if len(key) < 24:
        raise ValueError("Owner key must be at least 24 characters. Use a random secret.")
    server = ThreadingHTTPServer((host, port), DeskHandler)
    server.daemon_threads = True
    server.cli_binary = resolve_binary(claw_path)
    server.csrf = secrets.token_urlsafe(32)
    server.owner_key = key
    server.generated_key = not bool(supplied)
    server.public_origin = public_origin
    server.prompt_lock = threading.BoundedSemaphore(1)
    server.process_lock = threading.Lock()
    server.active_processes = set()
    server.stopping = threading.Event()
    server.attachments = AttachmentStore()
    redirect_origin = public_origin or f"http://localhost:{server.server_port}"
    server.google = GoogleIntegration(redirect_uri=redirect_origin+"/api/google/callback")
    server.tasks = None  # enabled in main(), not during standalone HTTP regression tests
    server.agents = AgentManager(server.stopping,
                                 lambda prompt,mode:run_claw_prompt(server,prompt,mode,wait=True))
    return server


def main():
    parser = argparse.ArgumentParser(description="iPhone-ready owner-gated UI for the Claw CLI")
    parser.add_argument("--port", type=int, default=8765, help="localhost port (default 8765)")
    parser.add_argument("--claw", help="explicit path to the compiled claw binary")
    parser.add_argument(
        "--public-origin", default=os.environ.get("CLAW_DESK_PUBLIC_ORIGIN"),
        help="Exact PRIVATE Tailscale Serve URL, e.g. https://device.tailnet.ts.net",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    server = make_server(port=args.port, claw_path=args.claw, public_origin=args.public_origin)
    server.tasks = TaskStore()
    scheduler = threading.Thread(
        target=server.tasks.loop,
        args=(lambda task:execute_scheduled_task(server,task),server.stopping),
        daemon=True,name="claw-desk-scheduler")
    scheduler.start()
    print(f"Claw Desk host: http://127.0.0.1:{server.server_port}", flush=True)
    if server.generated_key:
        print("OWNER KEY (enter on iPhone, keep private): " + server.owner_key, flush=True)
    else:
        print("OWNER KEY: loaded from CLAW_DESK_OWNER_KEY", flush=True)
    if server.cli_binary is None:
        print("CLI missing: cd rust && cargo build -p rusty-claude-cli", flush=True)
    if server.public_origin:
        print("iPhone private URL: " + server.public_origin, flush=True)
    print("Use Tailscale Serve, NEVER Tailscale Funnel. The red power button stops this server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        with server.process_lock:
            for process in list(server.active_processes):
                kill_process(process)
        server.server_close()
        server.tasks.close()


if __name__ == "__main__":
    main()
