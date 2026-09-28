"""End-to-end localhost HTTP tests for the expanded mobile feature routes."""
import base64
from datetime import datetime, timedelta, timezone
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location("claw_desk_http_features",HERE/"server.py")
server_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class HttpFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.binary=Path(self.temp.name)/"claw"
        self.binary.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "print(json.dumps({'message':'MOCK_AGENT_OK','args':sys.argv[1:]}))\n",
            encoding="utf-8"
        )
        self.binary.chmod(0o700)
        self.owner="integration-"+("k"*32)
        self.server=server_module.make_server(port=0,claw_path=str(self.binary),owner_key=self.owner)
        self.server.tasks=server_module.TaskStore(":memory:")
        self.thread=threading.Thread(target=self.server.serve_forever,
                                    kwargs={"poll_interval":0.01},daemon=True)
        self.thread.start()
        code,data=self.call("GET","/api/bootstrap")
        self.assertEqual(code,200)
        self.csrf=data["csrf"]

    def tearDown(self):
        if not self.server.stopping.is_set():
            self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.server.tasks.close()
        self.temp.cleanup()

    def call(self,method,path,data=None,authorized=False):
        con=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=15)
        headers={}
        if authorized:
            headers["X-Claw-Desk-Owner-Key"]=self.owner
            headers["X-Claw-Desk-Token"]=self.csrf
        body=None
        if data is not None:
            headers["Content-Type"]="application/json"
            body=json.dumps(data)
        try:
            con.request(method,path,body=body,headers=headers)
            response=con.getresponse()
            code=response.status
            payload=json.loads(response.read().decode("utf-8"))
            return code,payload
        finally:
            con.close()

    def test_attachment_upload_and_mock_multimodal_chat(self):
        payload={"name":"image.png","media_type":"image/png",
                 "data":base64.b64encode(b"\x89PNG\r\n\x1a\nFAKE").decode()}
        self.assertEqual(self.call("POST","/api/files/upload",payload)[0],403)
        code,file=self.call("POST","/api/files/upload",payload,True)
        self.assertEqual(code,200)
        self.assertEqual(file["media_type"],"image/png")
        self.assertNotIn("data",file)
        with patch.object(server_module,"provider_multimodal",return_value={
            "message":"A mock picture.","used_claw_tools":False
        }) as provider:
            code,reply=self.call("POST","/api/chat",{
                "message":"Describe this picture","attachments":[file["id"]],"mode":"read-only"
            },True)
        self.assertEqual(code,200)
        self.assertFalse(reply["used_claw_tools"])
        self.assertEqual(provider.call_args.args[2][0]["mime"],"image/png")
        self.assertEqual(self.call("POST","/api/chat",{
            "message":"hi","attachments":["missing"]
        },True)[0],400)

    def test_google_authorization_read_and_explicit_writes(self):
        self.assertEqual(self.call("GET","/api/google/status")[0],403)
        code,status=self.call("GET","/api/google/status",authorized=True)
        self.assertEqual(code,200)
        self.assertIn("connected",status)
        with patch.object(self.server.google,"start",return_value="https://accounts.google.com/mock"):
            code,response=self.call("POST","/api/google/connect",authorized=True)
        self.assertEqual(code,200)
        self.assertIn("accounts.google.com",response["url"])
        with patch.object(self.server.google,"drive",return_value={"files":[{"name":"Doc"}]}) as search:
            code,response=self.call("GET","/api/google/drive?q=Doc",authorized=True)
        self.assertEqual(code,200)
        search.assert_called_once_with("Doc")
        with patch.object(self.server.google,"create_draft",
                          return_value={"draft_created":True,"sent":False}) as draft:
            code,response=self.call("POST","/api/google/draft",{
                "to":"user@example.com","subject":"Hello","body":"Draft","confirm":True
            },True)
        self.assertEqual(code,200)
        self.assertFalse(response["sent"])
        self.assertTrue(draft.call_args.args[3])
        with patch.object(self.server.google,"create_event",return_value={"created":True}) as event:
            code,response=self.call("POST","/api/google/event",{
                "summary":"Appointment","start":"2027-01-01T10:00:00Z",
                "end":"2027-01-01T11:00:00Z","confirm":True
            },True)
        self.assertEqual(code,200)
        self.assertTrue(event.call_args.args[3])

    def test_scheduled_task_crud_and_3_agent_execution(self):
        date=(datetime.now(timezone.utc)+timedelta(minutes=3)).isoformat()
        code,task=self.call("POST","/api/tasks/create",{
            "kind":"prompt","prompt":"Assess roadmap","run_at":date,"interval_seconds":86400
        },True)
        self.assertEqual(code,200)
        self.assertEqual(task["state"],"scheduled")
        code,rows=self.call("GET","/api/tasks",authorized=True)
        self.assertEqual(code,200)
        self.assertEqual(rows["tasks"][0]["prompt"],"Assess roadmap")
        code,deleted=self.call("POST","/api/tasks/delete",{"id":task["id"]},True)
        self.assertEqual(code,200)
        self.assertTrue(deleted["deleted"])
        code,error=self.call("POST","/api/agents/start",{
            "prompt":"Improve tests","mode":"workspace-write","confirm_write":False
        },True)
        self.assertEqual(code,400)
        code,job=self.call("POST","/api/agents/start",{
            "prompt":"Improve tests","mode":"read-only"
        },True)
        self.assertEqual(code,200)
        deadline=time.time()+4
        result={}
        while time.time()<deadline:
            code,result=self.call("GET","/api/agents/job?id="+job["id"],authorized=True)
            if result["state"]!="running":
                break
            time.sleep(.02)
        self.assertEqual(result["state"],"done")
        self.assertEqual([s["role"] for s in result["steps"]],
                         ["Architect","Executor","Reviewer"])
        self.assertTrue(all(s["mode"]=="read-only" for s in result["steps"]))


if __name__=="__main__":
    unittest.main()
