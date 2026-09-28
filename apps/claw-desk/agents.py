"""Bounded three-role orchestration for the existing Claw CLI.

Each role receives a separate prompt/run; no fake parallel agents or false
claims of test execution. Review is read-only even when the executor can write.
"""
import secrets
import threading
import time


class AgentError(Exception):
    pass


ROLES = [
    ("Architect", "Inspect the problem. Produce a short actionable plan, relevant files and acceptance criteria. Do not change files."),
    ("Executor", "Use the architecture plan. Perform the requested implementation ONLY when permissions allow; otherwise propose a precise implementation without editing. Keep the change scoped."),
    ("Reviewer", "Independently inspect the requested change and the prior plan/execution. Identify gaps, evidence and any unverified claims. Never claim a test passed without actually running it. Do not edit files."),
]


class AgentManager:
    def __init__(self, stopping, executor):
        self.stopping = stopping
        self.executor = executor
        self.lock = threading.RLock()
        self.jobs = {}

    def start(self, prompt, mode="read-only", confirm_write=False):
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 5000:
            raise AgentError("Enter a task of 1–5000 characters.")
        if mode not in ("read-only", "workspace-write"):
            raise AgentError("Unsupported mode.")
        if mode == "workspace-write" and confirm_write is not True:
            raise AgentError("Explicit workspace-write confirmation is required.")
        if self.stopping.is_set():
            raise AgentError("Claw Desk is shut down.")
        with self.lock:
            active = sum(j["state"] == "running" for j in self.jobs.values())
            if active >= 2:
                raise AgentError("Two workflows are already running.")
            identifier = secrets.token_urlsafe(16)
            self.jobs[identifier] = {
                "id":identifier,"state":"running","task":prompt.strip(),
                "mode":mode,"phase":"Queued","steps":[],"created_at":time.time(),
                "note":"Three sequential agent roles; no independent multi-model consensus.",
            }
        thread = threading.Thread(target=self._run, args=(identifier,),daemon=True)
        thread.start()
        return {"id":identifier,"state":"running"}

    def get(self, identifier):
        if not isinstance(identifier, str) or len(identifier)>90:
            raise AgentError("Invalid agent job identifier.")
        with self.lock:
            job = self.jobs.get(identifier)
            if not job:
                raise AgentError("Agent job expired or not found.")
            return {**job,"steps":[dict(s) for s in job["steps"]]}

    def _run(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            prompt, mode = job["task"], job["mode"]
        preceding = []
        for role, directive in ROLES:
            if self.stopping.is_set():
                with self.lock:
                    job.update(state="cancelled",phase="Owner shutdown")
                return
            permission = mode if role == "Executor" else "read-only"
            context = "\n\n".join(
                (name + " OUTPUT:\n" + output[:5000]) for name,output in preceding
            )
            task = (
                "You are the " + role + " in a three-role development workflow.\n"
                + directive + "\nYou are handling the user's Claw Desk project.\n"
                + "ORIGINAL USER TASK:\n" + prompt + "\n"
                + ("PRIOR ROLE RESULTS:\n" + context if context else "")
            )
            with self.lock:
                job["phase"] = role
            try:
                code, result = self.executor(task, permission)
            except Exception as exc:
                code, result = 502, {"error":"Execution exception: "+type(exc).__name__}
            if self.stopping.is_set():
                with self.lock:
                    job.update(state="cancelled",phase="Owner shutdown")
                return
            text = str(result.get("message") or result.get("error") or "No response")[:10000]
            with self.lock:
                job["steps"].append({"role":role,"mode":permission,
                                     "status":"ok" if code==200 else "error",
                                     "output":text})
            preceding.append((role,text))
            if code != 200:
                with self.lock:
                    job.update(state="error",phase="Stopped on role error")
                return
        with self.lock:
            job.update(state="done",phase="Complete")
