"""Private transient attachments, multimodal Anthropic chat and optional transcription."""
import base64
import binascii
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_FILE_BYTES = 6_000_000
MAX_AUDIO_BYTES = 8_000_000
SUPPORTED = {"image/png", "image/jpeg", "image/webp", "application/pdf", "text/plain"}
AUDIO = {"audio/mp4", "audio/m4a", "audio/x-m4a", "audio/wav", "audio/webm", "audio/mpeg", "audio/mp3"}
ATTACHMENT_LIFETIME = 3600


class MediaError(Exception):
    pass


def decode_input(payload, allowed, max_bytes):
    if not isinstance(payload, dict):
        raise MediaError("Invalid upload payload.")
    name = payload.get("name")
    mime = payload.get("media_type")
    raw = payload.get("data")
    if not isinstance(name, str) or not 1 <= len(name) <= 100 or not isinstance(mime, str):
        raise MediaError("Provide a short filename and supported media type.")
    if not isinstance(raw, str) or len(raw) > (max_bytes * 4 // 3) + 8:
        raise MediaError("Attachment exceeds the allowed size.")
    mime = mime.split(";",1)[0].lower().strip()
    if mime not in allowed:
        raise MediaError("File type is unsupported.")
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MediaError("Invalid base64 attachment.") from exc
    if not data or len(data) > max_bytes:
        raise MediaError("Attachment is empty or too large.")
    if mime == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MediaError("PNG signature mismatch.")
    if mime == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise MediaError("JPEG signature mismatch.")
    if mime == "image/webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise MediaError("WEBP signature mismatch.")
    if mime == "application/pdf" and not data.startswith(b"%PDF-"):
        raise MediaError("PDF signature mismatch.")
    if mime == "text/plain":
        try:
            data.decode("utf-8")
        except UnicodeError as exc:
            raise MediaError("Text attachments must be UTF-8.") from exc
    # The original uploaded filename is only metadata, never used as a filesystem path.
    return {"name":Path(name.replace("\\","/")).name[:80], "mime":mime, "bytes":data}


class AttachmentStore:
    """Non-persistent in-memory owner uploads; never exposes host file paths."""
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def put(self, payload):
        file = decode_input(payload, SUPPORTED, MAX_FILE_BYTES)
        with self.lock:
            self._clean()
            if len(self.items) >= 10:
                raise MediaError("Too many pending attachments. Send a message or wait for expiry.")
            identifier = secrets.token_urlsafe(24)
            self.items[identifier] = {**file, "expires":time.time() + ATTACHMENT_LIFETIME}
            return {"id":identifier, "name":file["name"], "media_type":file["mime"],
                    "bytes":len(file["bytes"]), "expires_in_seconds":ATTACHMENT_LIFETIME}

    def _clean(self):
        now = time.time()
        for identifier in list(self.items):
            if self.items[identifier]["expires"] < now:
                del self.items[identifier]

    def get_many(self, identifiers):
        if not isinstance(identifiers, list) or len(identifiers) > 3:
            raise MediaError("Select up to three attachments.")
        if any(not isinstance(x, str) or len(x) > 90 for x in identifiers):
            raise MediaError("Invalid attachment ID.")
        with self.lock:
            self._clean()
            chosen = []
            for identifier in identifiers:
                item = self.items.get(identifier)
                if not item:
                    raise MediaError("Attachment expired or not found. Upload it again.")
                chosen.append(item)
            return chosen


def provider_multimodal(message, history, files):
    """Direct Anthropic multimodal request; does NOT execute Claw tools.

    Source photos/PDFs leave the host ONLY after the user explicitly attaches
    and submits them. Keep API tokens server-side.
    """
    key = os.environ.get("ANTHROPIC_API_KEY","")
    if not key:
        raise MediaError("Photo/PDF understanding needs ANTHROPIC_API_KEY on the trusted host.")
    if not isinstance(message, str) or len(message) > 6000:
        raise MediaError("Invalid message.")
    content = [{"type":"text", "text":message}]
    for file in files:
        mime = file["mime"]
        if mime == "text/plain":
            content.append({"type":"text", "text":"ATTACHED TEXT FILE: " + file["name"] + "\n"
                            + file["bytes"].decode("utf-8")[:60_000]})
        else:
            kind = "document" if mime == "application/pdf" else "image"
            content.append({"type":kind,"source":{
                "type":"base64","media_type":mime,
                "data":base64.b64encode(file["bytes"]).decode("ascii"),
            }})
    messages = []
    for role, text in history:
        if role in ("user","assistant") and text:
            messages.append({"role":role, "content":[{"type":"text","text":text[:2000]}]})
    messages.append({"role":"user", "content":content})
    model = os.environ.get("CLAW_DESK_MULTIMODAL_MODEL","claude-sonnet-4-6")
    body = json.dumps({"model":model,"max_tokens":1800,"messages":messages}).encode("utf-8")
    req = Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key":key,"anthropic-version":"2023-06-01",
        "content-type":"application/json","Accept":"application/json",
    })
    try:
        with urlopen(req, timeout=120) as response:
            answer = json.loads(response.read(4_000_000).decode("utf-8"))
    except (HTTPError, URLError, UnicodeError, ValueError) as exc:
        raise MediaError("Multimodal provider request failed. Check provider key, model and file support.") from exc
    text = "\n".join(block.get("text","") for block in answer.get("content",[])
                     if block.get("type") == "text").strip()
    if not text:
        raise MediaError("The provider returned no readable text.")
    return {"message":text,"model":answer.get("model",model),
            "attachment_count":len(files),"used_claw_tools":False}


def transcribe(payload):
    """Optional recorded audio via OpenAI Audio API. No audio is stored on disk."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise MediaError("Audio transcription needs OPENAI_API_KEY configured on the trusted host.")
    file = decode_input(payload, AUDIO, MAX_AUDIO_BYTES)
    mime, data = file["mime"], file["bytes"]
    if mime in ("audio/mp4","audio/m4a","audio/x-m4a") and b"ftyp" not in data[:12]:
        raise MediaError("Audio container signature mismatch.")
    if mime == "audio/webm" and not data.startswith(b"\x1a\x45\xdf\xa3"):
        raise MediaError("Audio container signature mismatch.")
    if mime == "audio/wav" and not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"):
        raise MediaError("Audio container signature mismatch.")
    boundary = "ClawDesk" + secrets.token_hex(15)
    def field(name, value):
        return (
            "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name +
            "\"\r\n\r\n" + value + "\r\n"
        ).encode("utf-8")
    pieces = [
        field("model", "whisper-1"),
        ("--"+boundary+"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio\"\r\n"
         "Content-Type: "+mime+"\r\n\r\n").encode("ascii"),
        data, b"\r\n",
        ("--"+boundary+"--\r\n").encode("ascii"),
    ]
    req = Request("https://api.openai.com/v1/audio/transcriptions",data=b"".join(pieces),
                  method="POST",headers={"Authorization":"Bearer "+key,
                  "Content-Type":"multipart/form-data; boundary="+boundary})
    try:
        with urlopen(req,timeout=80) as response:
            result=json.loads(response.read(100_000).decode("utf-8"))
    except (HTTPError, URLError, ValueError, UnicodeError) as exc:
        raise MediaError("Audio transcription failed. Check microphone recording and provider access.") from exc
    return {"text":str(result.get("text",""))[:6000]}
