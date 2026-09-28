"""Offline tests for the new private media, Google and automation modules."""
import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest import TestCase, main
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import agents
import google_integration as google
import media
import scheduler


class MediaTests(TestCase):
    def sample(self, mime, data, name="photo"):
        return {"name":name,"media_type":mime,"data":base64.b64encode(data).decode()}

    def test_uploads_validate_media_and_limit(self):
        png=b"\x89PNG\r\n\x1a\n"+b"data"
        store=media.AttachmentStore()
        one=store.put(self.sample("image/png",png,"../photo.png"))
        self.assertEqual(one["name"],"photo.png")
        self.assertEqual(store.get_many([one["id"]])[0]["bytes"],png)
        with self.assertRaises(media.MediaError):
            store.put(self.sample("image/png",b"not a png"))
        with self.assertRaises(media.MediaError):
            store.put(self.sample("application/pdf",b"<script>fake PDF</script>"))
        pdf=store.put(self.sample("application/pdf",b"%PDF-1.4\nbody","file.pdf"))
        self.assertEqual(pdf["media_type"],"application/pdf")
        with self.assertRaises(media.MediaError):
            store.get_many([one["id"]]*4)
        with self.assertRaises(media.MediaError):
            store.get_many(["bad-id"])
        with self.assertRaises(media.MediaError):
            store.put(self.sample("image/png",b"\x89PNG\r\n\x1a\n"+b"x"*(media.MAX_FILE_BYTES+1)))
        for _ in range(8):
            store.put(self.sample("text/plain",b"note","n.txt"))
        with self.assertRaises(media.MediaError):
            store.put(self.sample("text/plain",b"note","n.txt"))

    def test_provider_multimodal_and_host_only_secret(self):
        file=media.decode_input(self.sample("image/png",b"\x89PNG\r\n\x1a\np"),media.SUPPORTED,media.MAX_FILE_BYTES)
        seen=[]
        def fake_open(req,timeout=0):
            seen.append(req)
            return io.BytesIO(b'{"model":"test-model","content":[{"type":"text","text":"I see a chart."}]}')
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY":"fake-private-key"}):
            with patch.object(media,"urlopen",side_effect=fake_open):
                result=media.provider_multimodal("Inspect",[],[file])
        self.assertEqual(result["message"],"I see a chart.")
        self.assertFalse(result["used_claw_tools"])
        payload=json.loads(seen[0].data)
        self.assertEqual(payload["messages"][0]["content"][1]["type"],"image")
        self.assertNotIn("fake-private-key",json.dumps(result))
        with patch.dict(os.environ,{"ANTHROPIC_API_KEY":""}):
            with self.assertRaises(media.MediaError):
                media.provider_multimodal("Inspect",[],[file])

    def test_audio_transcribe_mock(self):
        audio=b"\x00\x00\x00\x18ftypM4A "+b"x"*30
        seen=[]
        def fake_open(req,timeout=0):
            seen.append(req)
            return io.BytesIO(b'{"text":"Dictated hello"}')
        with patch.dict(os.environ,{"OPENAI_API_KEY":"fake-stt-key"}):
            with patch.object(media,"urlopen",side_effect=fake_open):
                response=media.transcribe(self.sample("audio/mp4",audio,"voice.m4a"))
        self.assertEqual(response["text"],"Dictated hello")
        self.assertIn(b"whisper-1",seen[0].data)
        self.assertIn(b"ftypM4A",seen[0].data)
        with patch.dict(os.environ,{"OPENAI_API_KEY":""}):
            with self.assertRaises(media.MediaError):
                media.transcribe(self.sample("audio/mp4",audio,"voice.m4a"))


class GoogleTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{
            "CLAW_DESK_GOOGLE_CLIENT_ID":"test-client.apps.googleusercontent.com",
            "CLAW_DESK_GOOGLE_CLIENT_SECRET":"fake-secret",
        })
        self.env.start()
        self.google=google.GoogleIntegration(
            redirect_uri="http://localhost:8765/api/google/callback",data_dir=self.temp.name
        )

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_pkce_state_single_use_token_and_private_storage(self):
        self.assertTrue(self.google.configured)
        link=self.google.start()
        self.assertIn("code_challenge_method=S256",link)
        self.assertNotIn("fake-secret",link)
        from urllib.parse import parse_qs,urlsplit
        state=parse_qs(urlsplit(link).query)["state"][0]
        with patch.object(google,"form_post",return_value={
            "access_token":"access","refresh_token":"refresh","expires_in":3600
        }) as mock_post:
            self.assertTrue(self.google.callback(state,"authorization-code"))
            args=mock_post.call_args[0][1]
            self.assertTrue(args["code_verifier"])
        self.assertTrue(self.google.connected)
        saved=Path(self.temp.name)/"google-oauth.json"
        self.assertEqual(json.loads(saved.read_text())["refresh_token"],"refresh")
        if os.name!="nt":
            self.assertEqual(saved.stat().st_mode & 0o777,0o600)
        with self.assertRaises(google.GoogleError):
            self.google.callback(state,"replay")
        self.assertEqual(self.google.token(),"access")

    def test_drive_inbox_drafts_and_events(self):
        self.google.credentials={"refresh_token":"refresh","access_token":"access",
                                 "expires_at":time.time()+1000}
        calls=[]
        def api(path,params=None,payload=None,method=None):
            calls.append((path,params,payload))
            if path.startswith("/drive/"):
                return {"files":[{"name":"Budget.xlsx","id":"id"}]}
            if path.endswith("/messages"):
                return {"messages":[{"id":"m1"}]}
            if "/messages/" in path:
                return {"payload":{"headers":[{"name":"Subject","value":"Important"}]},
                        "threadId":"t1","snippet":"Hello"}
            if path.endswith("/drafts"):
                return {"id":"draft1"}
            if path.endswith("/events") and payload:
                return {"id":"event1"}
            if path.endswith("/events"):
                return {"items":[{"id":"ev","summary":"Meeting","start":{"dateTime":"2027-01-01T10:00:00Z"}}]}
            return {}
        self.google.api=api
        self.assertEqual(self.google.drive("Budget")["files"][0]["name"],"Budget.xlsx")
        self.assertEqual(self.google.mail()["messages"][0]["subject"],"Important")
        self.assertEqual(self.google.calendar()["events"][0]["summary"],"Meeting")
        with self.assertRaises(google.GoogleError):
            self.google.create_draft("user@example.com","Hi","Hello",False)
        self.assertFalse(self.google.create_draft("user@example.com","Hi","Hello",True)["sent"])
        draft_payload=[x[2] for x in calls if x[0].endswith("/drafts")][-1]
        self.assertIn("raw",draft_payload["message"])
        start=datetime.now(timezone.utc)+timedelta(hours=3)
        end=start+timedelta(hours=1)
        with self.assertRaises(google.GoogleError):
            self.google.create_event("Meeting",start.isoformat(),end.isoformat(),False)
        self.assertTrue(self.google.create_event("Meeting",start.isoformat(),end.isoformat(),True)["created"])


class SchedulerTests(TestCase):
    def setUp(self):
        self.tasks=scheduler.TaskStore(":memory:")

    def tearDown(self):
        self.tasks.close()

    def test_schedule_runs_and_persists_readonly_result(self):
        future=datetime.now(timezone.utc)+timedelta(minutes=3)
        created=self.tasks.create({"kind":"prompt","prompt":"Summarize project",
                                   "run_at":future.isoformat(),"interval_seconds":0})
        self.assertEqual(self.tasks.list()["tasks"][0]["state"],"scheduled")
        with self.tasks.lock:
            self.tasks.db.execute("UPDATE tasks SET next_at=? WHERE id=?",
                                  (time.time()-1,created["id"]))
            self.tasks.db.commit()
        calls=[]
        self.tasks.run_due(lambda task:calls.append(task["prompt"]) or "Summary completed",threading.Event())
        self.assertEqual(calls,["Summarize project"])
        self.assertEqual(self.tasks.list()["tasks"][0]["state"],"done")
        self.assertIn("Summary",self.tasks.list()["tasks"][0]["result"])
        self.assertTrue(self.tasks.remove(created["id"])["deleted"])

    def test_recurring_and_validation(self):
        future=datetime.now(timezone.utc)+timedelta(minutes=3)
        created=self.tasks.create({"kind":"calendar_digest","prompt":"",
                                   "run_at":future.isoformat(),"interval_seconds":3600})
        with self.tasks.lock:
            self.tasks.db.execute("UPDATE tasks SET next_at=? WHERE id=?",
                                  (time.time()-2,created["id"]))
            self.tasks.db.commit()
        self.tasks.run_due(lambda task:"Events summarized",threading.Event())
        row=self.tasks.list()["tasks"][0]
        self.assertEqual(row["state"],"scheduled")
        self.assertGreater(row["next_at"],time.time())
        for bad in ({"kind":"prompt","prompt":"","run_at":future.isoformat()},
                    {"kind":"prompt","prompt":"x","run_at":future.isoformat(),"interval_seconds":30},
                    {"kind":"unknown","prompt":"x","run_at":future.isoformat()}):
            with self.assertRaises(scheduler.TaskError):
                self.tasks.create(bad)


class CouncilTests(TestCase):
    def test_separate_role_permissions_and_confirmation(self):
        calls=[]
        def fake_executor(task,mode):
            calls.append((task,mode))
            return 200,{"message":"A completed "+str(len(calls))}
        manager=agents.AgentManager(threading.Event(),fake_executor)
        with self.assertRaises(agents.AgentError):
            manager.start("build app","workspace-write",False)
        job=manager.start("build app","workspace-write",True)
        deadline=time.time()+3
        while time.time()<deadline and manager.get(job["id"])["state"]=="running":
            time.sleep(.01)
        result=manager.get(job["id"])
        self.assertEqual(result["state"],"done")
        self.assertEqual([x[1] for x in calls],["read-only","workspace-write","read-only"])
        self.assertEqual([x["role"] for x in result["steps"]],["Architect","Executor","Reviewer"])


if __name__=="__main__":
    main()
