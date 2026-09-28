"""Optional Google OAuth + narrowly scoped Drive/Gmail/Calendar integration.

No ChatGPT connector credentials are assumed or reused. Each host owner explicitly
authorizes a separately registered Google Cloud OAuth web client.
"""
import base64
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com"
DEFAULT_DIR = Path.home() / ".claw-desk"


class GoogleError(Exception):
    pass


def http_json(url, payload=None, headers=None, method=None, timeout=12):
    head = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        head["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=head, method=method or ("POST" if body else "GET"))
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(2_000_000)
            return json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, ValueError, UnicodeError) as exc:
        raise GoogleError("Google request failed. Check OAuth permissions and network connection.") from exc


def form_post(url, fields):
    data = urlencode(fields).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read(256_000).decode("utf-8"))
    except (HTTPError, URLError, ValueError, UnicodeError) as exc:
        raise GoogleError("Google token exchange failed. Check client credentials and registered redirect URI.") from exc


class GoogleIntegration:
    def __init__(self, redirect_uri=None, data_dir=None):
        self.client_id = os.environ.get("CLAW_DESK_GOOGLE_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("CLAW_DESK_GOOGLE_CLIENT_SECRET", "").strip()
        self.redirect_uri = redirect_uri
        self.data_dir = Path(data_dir or DEFAULT_DIR).expanduser()
        self.file = self.data_dir / "google-oauth.json"
        self.lock = threading.RLock()
        self.pending = {}
        self.credentials = None
        if self.file.exists():
            try:
                self.credentials = json.loads(self.file.read_text("utf-8"))
            except (OSError, ValueError):
                self.credentials = None

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    @property
    def connected(self):
        return bool(self.credentials and self.credentials.get("refresh_token"))

    def status(self):
        return {"configured": self.configured, "connected": self.connected,
                "features": ["Drive file search", "Gmail inbox summaries", "Gmail draft creation",
                             "Calendar read", "Calendar event creation"]}

    def _persist(self):
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self.data_dir.chmod(0o700)
        temp = self.data_dir / ("google-oauth." + secrets.token_hex(8) + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.credentials, stream)
            os.replace(temp, self.file)
            if os.name != "nt":
                self.file.chmod(0o600)
        finally:
            if temp.exists():
                temp.unlink()

    def start(self):
        if not self.configured:
            raise GoogleError("Set CLAW_DESK_GOOGLE_CLIENT_ID, CLAW_DESK_GOOGLE_CLIENT_SECRET and a matching private HTTPS origin on the host.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.lock:
            now = time.time()
            self.pending = {k:v for k,v in self.pending.items() if v["expires"] > now}
            self.pending[state] = {"verifier": verifier, "expires": now + 600}
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        return AUTH_URL + "?" + urlencode(params)

    def callback(self, state, code):
        if not state or not code:
            raise GoogleError("Authorization was not completed.")
        with self.lock:
            item = self.pending.pop(state, None)
        if not item or item["expires"] < time.time():
            raise GoogleError("Invalid or expired Google authorization attempt.")
        token = form_post(TOKEN_URL, {
            "grant_type":"authorization_code", "code":code,
            "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "code_verifier":item["verifier"],
        })
        if not isinstance(token, dict) or not token.get("access_token"):
            raise GoogleError("Google did not return an access token.")
        with self.lock:
            prior_refresh = (self.credentials or {}).get("refresh_token")
            self.credentials = {
                "access_token":token["access_token"],
                "refresh_token":token.get("refresh_token") or prior_refresh,
                "expires_at":time.time() + int(token.get("expires_in", 3600)) - 90,
                "scope": token.get("scope", ""),
            }
            if not self.credentials["refresh_token"]:
                raise GoogleError("No refresh token returned. Revoke the prior app grant in your Google account and reconnect.")
            self._persist()
        return True

    def token(self):
        with self.lock:
            if not self.connected:
                raise GoogleError("Connect your Google account before using this feature.")
            if time.time() < float(self.credentials.get("expires_at", 0)):
                return self.credentials["access_token"]
            result = form_post(TOKEN_URL, {
                "client_id":self.client_id, "client_secret":self.client_secret,
                "refresh_token":self.credentials["refresh_token"],
                "grant_type":"refresh_token",
            })
            if not result.get("access_token"):
                raise GoogleError("Google token refresh failed. Reconnect your account.")
            self.credentials["access_token"] = result["access_token"]
            self.credentials["expires_at"] = time.time() + int(result.get("expires_in",3600)) - 90
            return result["access_token"]

    def api(self, path, params=None, payload=None, method=None):
        url = API + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        return http_json(url, payload, {"Authorization":"Bearer " + self.token()}, method=method)

    def drive(self, query=""):
        if not isinstance(query, str) or len(query) > 120:
            raise GoogleError("Drive search is limited to 120 characters.")
        expression = "trashed = false"
        if query.strip():
            escaped = query.strip().replace("\\","\\\\").replace("'","\\'")
            expression += " and name contains '" + escaped + "'"
        data = self.api("/drive/v3/files", {
            "q": expression, "pageSize":20,
            "fields":"nextPageToken,files(id,name,mimeType,description,modifiedTime,webViewLink,size)",
            "orderBy":"modifiedTime desc",
        })
        return {"files":data.get("files", [])}

    def mail(self):
        result = self.api("/gmail/v1/users/me/messages", {"maxResults":10, "q":"in:inbox newer_than:30d"})
        messages = []
        for item in result.get("messages", [])[:10]:
            mid = item.get("id")
            if not mid:
                continue
            detail = self.api("/gmail/v1/users/me/messages/" + quote(mid, safe=""),
                              {"format":"metadata", "metadataHeaders":["Subject","From","Date"]})
            headers = {h["name"].lower():h.get("value","") for h in detail.get("payload",{}).get("headers",[])
                       if "name" in h}
            messages.append({"id":mid, "threadId":detail.get("threadId"),
                             "subject":headers.get("subject","(no subject)"),
                             "from":headers.get("from",""),
                             "date":headers.get("date",""),
                             "snippet":detail.get("snippet","")[:300]})
        return {"messages":messages}

    def create_draft(self, to, subject, body, confirm=False):
        if confirm is not True or not all(isinstance(x,str) for x in (to,subject,body)):
            raise GoogleError("Explicit confirmation and valid draft fields are required.")
        if not (3 <= len(to) <= 254 and "@" in to and "\n" not in to
                and 1 <= len(subject) <= 180 and "\n" not in subject and 1 <= len(body) <= 10_000):
            raise GoogleError("Invalid draft recipient, subject, or body.")
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        result = self.api("/gmail/v1/users/me/drafts",payload={"message":{"raw":encoded}})
        return {"draft_created": True, "id": result.get("id"), "sent": False}

    def calendar(self):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
        result = self.api("/calendar/v3/calendars/primary/events",
                          {"timeMin":now, "maxResults":15, "singleEvents":"true", "orderBy":"startTime"})
        return {"events":[{
            "id":event.get("id"), "summary":event.get("summary","(untitled)"),
            "start":event.get("start",{}), "end":event.get("end",{}),
            "htmlLink":event.get("htmlLink",""),
            "description":event.get("description","")[:400]
        } for event in result.get("items",[])]}

    def create_event(self, summary, start, end, confirm=False):
        if confirm is not True or not all(isinstance(v,str) for v in (summary,start,end)):
            raise GoogleError("Explicit confirmation and event fields are required.")
        if not 1 <= len(summary) <= 180:
            raise GoogleError("Event title must contain 1–180 characters.")
        try:
            a = datetime.fromisoformat(start.replace("Z","+00:00"))
            b = datetime.fromisoformat(end.replace("Z","+00:00"))
            if a.tzinfo is None or b.tzinfo is None or not a < b or (b-a)>timedelta(days=7):
                raise ValueError()
        except ValueError as exc:
            raise GoogleError("Use valid timezone-aware ISO dates with end after start (maximum seven days).") from exc
        result = self.api("/calendar/v3/calendars/primary/events",
                          payload={"summary":summary,"start":{"dateTime":a.isoformat()},
                                   "end":{"dateTime":b.isoformat()}})
        return {"created":True,"id":result.get("id"),"htmlLink":result.get("htmlLink")}
