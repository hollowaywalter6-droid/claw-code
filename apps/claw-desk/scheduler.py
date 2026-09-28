"""SQLite-backed recurring jobs. Owner-only setup, read-only execution.

Tasks execute only while the trusted host is running. The mobile app polls
stored results; this module never secretly sends mail, posts notifications or
performs workspace-write actions.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

VALID_KINDS = ("prompt", "inbox_digest", "calendar_digest", "combined_digest")
VALID_INTERVALS = (0, 3600, 86400, 604800)
MAX_TASKS = 30


class TaskError(Exception):
    pass


class TaskStore:
    def __init__(self, db_path=None):
        folder = Path.home() / ".claw-desk"
        if db_path is None:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                folder.chmod(0o700)
            db_path = folder / "scheduled-tasks.sqlite3"
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("""CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, prompt TEXT NOT NULL,
                next_at REAL NOT NULL, interval_seconds INTEGER NOT NULL,
                state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                last_at REAL, created_at REAL NOT NULL)""")
            self.db.commit()
            # Recover tasks interrupted by an earlier host shutdown.
            self.db.execute("UPDATE tasks SET state='scheduled' WHERE state='running'")
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def list(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,kind,prompt,next_at,interval_seconds,state,result,last_at,created_at "
                "FROM tasks ORDER BY created_at DESC"
            ).fetchall()
            return {"tasks":[dict(row) for row in rows]}

    def create(self, value):
        if not isinstance(value, dict):
            raise TaskError("Expected an object.")
        kind = value.get("kind")
        prompt = value.get("prompt","")
        date = value.get("run_at")
        interval = value.get("interval_seconds", 0)
        if kind not in VALID_KINDS or not isinstance(prompt, str) or len(prompt) > 3000:
            raise TaskError("Choose a supported task kind and a prompt under 3000 characters.")
        if kind == "prompt" and not prompt.strip():
            raise TaskError("Scheduled AI prompts cannot be empty.")
        if type(interval) is not int or interval not in VALID_INTERVALS:
            raise TaskError("Recurrence must be once, hourly, daily or weekly.")
        try:
            if not isinstance(date, str):
                raise ValueError()
            stamp = datetime.fromisoformat(date.replace("Z","+00:00"))
            if stamp.tzinfo is None:
                raise ValueError()
            next_at = stamp.timestamp()
        except ValueError as exc:
            raise TaskError("Use a timezone-aware ISO run date.") from exc
        now = time.time()
        if next_at < now + 90 or next_at > now + 365 * 86400:
            raise TaskError("Start time must be between 90 seconds and one year from now.")
        with self.lock:
            count = self.db.execute("SELECT count(*) FROM tasks").fetchone()[0]
            if count >= MAX_TASKS:
                raise TaskError("Task limit reached; delete an older task first.")
            identifier = secrets.token_urlsafe(16)
            self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,'scheduled','',NULL,?)",
                            (identifier,kind,prompt.strip(),next_at,interval,now))
            self.db.commit()
            return {"id":identifier,"kind":kind,"state":"scheduled",
                    "next_at":next_at,"interval_seconds":interval}

    def remove(self, identifier):
        if not isinstance(identifier,str) or len(identifier)>90:
            raise TaskError("Invalid task identifier.")
        with self.lock:
            row = self.db.execute("SELECT state FROM tasks WHERE id=?",(identifier,)).fetchone()
            if not row:
                raise TaskError("Task does not exist.")
            if row["state"] == "running":
                raise TaskError("A running task cannot be deleted until it finishes or the host stops.")
            self.db.execute("DELETE FROM tasks WHERE id=?",(identifier,))
            self.db.commit()
            return {"deleted":True}

    def run_due(self, handler, stopping, max_per_tick=2):
        if stopping.is_set():
            return
        now = time.time()
        with self.lock:
            due = self.db.execute(
                "SELECT * FROM tasks WHERE state='scheduled' AND next_at<=? ORDER BY next_at LIMIT ?",
                (now,max_per_tick)
            ).fetchall()
        for row in due:
            if stopping.is_set():
                break
            task = dict(row)
            with self.lock:
                updated = self.db.execute(
                    "UPDATE tasks SET state='running' WHERE id=? AND state='scheduled'",(task["id"],)
                )
                self.db.commit()
                if updated.rowcount != 1:
                    continue
            try:
                result = str(handler(task))[:10_000]
                success = True
            except Exception as exc:
                # Do not persist provider token, traceback or full private email content.
                result = ("Task failed: "+type(exc).__name__)[:400]
                success = False
            with self.lock:
                if stopping.is_set():
                    self.db.execute("UPDATE tasks SET state='scheduled' WHERE id=?",(task["id"],))
                elif task["interval_seconds"]:
                    next_at = task["next_at"]
                    while next_at <= time.time():
                        next_at += task["interval_seconds"]
                    self.db.execute(
                        "UPDATE tasks SET state='scheduled',result=?,last_at=?,next_at=? WHERE id=?",
                        (result,time.time(),next_at,task["id"])
                    )
                else:
                    self.db.execute(
                        "UPDATE tasks SET state=?,result=?,last_at=?,next_at=0 WHERE id=?",
                        ("done" if success else "error",result,time.time(),task["id"])
                    )
                self.db.commit()

    def loop(self, handler, stopping):
        while not stopping.is_set():
            self.run_due(handler,stopping)
            stopping.wait(10)
