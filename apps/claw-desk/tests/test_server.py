"""No-network regression tests for the Claw Desk mobile companion."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

SERVER_FILE = Path(__file__).resolve().parents[1] / "server.py"
spec = importlib.util.spec_from_file_location("claw_desk_server", SERVER_FILE)
desk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk)
KEY = "test-" + "z" * 40


class InputTests(unittest.TestCase):
    def test_chat_validation_and_bounds(self):
        self.assertEqual(desk.validate_chat({"message": " hello "}), ("hello", "read-only", []))
        self.assertEqual(desk.validate_chat({"message": "x", "mode": "workspace-write"})[1], "workspace-write")
        for bad in (
            None, {}, {"message": ""}, {"message": "x" * 6001},
            {"message": "hi", "mode": "danger-full-access"},
            {"message": "hi", "history": [{}]},
            {"message": "hi", "history": [{"role": "assistant", "content": "x" * 2001}]},
            {"message": "hi", "history": [{"role": "user", "content": "x"}] * 9},
        ):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                desk.validate_chat(bad)

    def test_context_format_and_response(self):
        prompt = desk.compose_prompt("new", [("user", "old"), ("assistant", "previous")])
        self.assertIn("CURRENT USER MESSAGE:\nnew", prompt)
        self.assertIn("ASSISTANT: previous", prompt)
        self.assertEqual(desk.compose_prompt("new", []), "new")
        self.assertEqual(desk.parse_cli_output('{"message":"hello"}')["message"], "hello")
        self.assertEqual(desk.parse_cli_output("plain error")["message"], "plain error")

    def test_missing_binary_and_origin_constraints(self):
        self.assertIsNone(desk.resolve_binary("/there/is/no/claw"))
        for invalid in ("http://host.tailnet.ts.net", "https://public.example.org",
                        "https://host.tailnet.ts.net/", "https://host.tailnet.ts.net/evil"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                desk.make_server(port=0, public_origin=invalid, owner_key=KEY)
        instance = desk.make_server(port=0, public_origin="https://host.tailnet.ts.net", owner_key=KEY)
        instance.server_close()
        with self.assertRaises(ValueError):
            desk.make_server(port=0, owner_key="short")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.binary = Path(self.temp.name) / "claw-fake"
        self.binary.write_text(
            "#!/usr/bin/env python3\n"
            "import json,sys,time\n"
            "a=sys.argv[1:]\n"
            "if 'slow-command' in ' '.join(a): time.sleep(20)\n"
            "print(json.dumps({'message':'FAKE_CLAW_OK','argv':a}))\n",
            encoding="utf-8",
        )
        self.binary.chmod(0o700)
        self.server = desk.make_server(port=0, claw_path=str(self.binary), owner_key=KEY)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        code, data = self.call("GET", "/api/bootstrap")
        self.assertEqual(code, 200)
        self.csrf = data["csrf"]
        self.assertTrue(data["requires_unlock"])

    def tearDown(self):
        if not self.server.stopping.is_set():
            self.server.shutdown()
        else:
            self.thread.join(timeout=2)
        self.server.server_close()
        self.temp.cleanup()

    def call(self, method, path, data=None, key=None, origin=None, csrf=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if key is not None:
            headers["X-Claw-Desk-Owner-Key"] = key
        if csrf is not None:
            headers["X-Claw-Desk-Token"] = csrf
        if origin is not None:
            headers["Origin"] = origin
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            code = response.status
            payload = json.loads(response.read().decode("utf-8"))
            return code, payload
        finally:
            connection.close()

    def test_security_and_unlock(self):
        self.assertEqual(self.call("GET", "/api/status")[0], 403)
        self.assertEqual(self.call("POST", "/api/unlock", key="incorrect", csrf=self.csrf)[0], 403)
        self.assertEqual(self.call("POST", "/api/unlock", key=KEY, csrf="incorrect")[0], 403)
        code, data = self.call("POST", "/api/unlock", key=KEY, csrf=self.csrf)
        self.assertEqual(code, 200)
        self.assertTrue(data["authorized"])
        self.assertEqual(self.call("GET", "/api/status", key=KEY, csrf=self.csrf)[0], 200)
        self.assertEqual(self.call("POST", "/api/shutdown", key="incorrect", csrf=self.csrf)[0], 403)

    def test_chat_contract_and_restricted_mode(self):
        code, data = self.call("POST", "/api/chat", data={
            "message": "hello", "mode": "read-only",
            "history": [{"role": "user", "content": "first"}],
        }, key=KEY, csrf=self.csrf)
        self.assertEqual(code, 200)
        self.assertEqual(data["message"], "FAKE_CLAW_OK")
        argv = data["data"]["argv"]
        self.assertIn("--compact", argv)
        self.assertIn("read-only", argv)
        self.assertIn("CURRENT USER MESSAGE:\nhello", argv[-1])
        self.assertEqual(self.call("POST", "/api/chat", data={
            "message":"x", "mode":"danger-full-access"
        }, key=KEY, csrf=self.csrf)[0], 400)

    def test_private_origin_and_static_assets(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.request("GET", "/", headers={"Origin":"https://attacker.example"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        for asset in ("/", "/app.js", "/app.css", "/manifest.webmanifest",
                      "/favicon.svg", "/sw.js", "/icon-180.png"):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            conn.request("GET", asset)
            result = conn.getresponse()
            self.assertEqual(result.status, 200, asset)
            self.assertIn("no-store", result.getheader("Cache-Control"))
            self.assertTrue(result.read())
            conn.close()

    def test_owner_kill_switch_cancels_active_cli(self):
        answer = []
        def send_slow():
            answer.append(self.call("POST", "/api/chat", data={
                "message": "slow-command"
            }, key=KEY, csrf=self.csrf))
        worker = threading.Thread(target=send_slow, daemon=True)
        worker.start()
        seen_active = False
        for _ in range(100):
            with self.server.process_lock:
                seen_active = bool(self.server.active_processes)
            if seen_active:
                break
            time.sleep(.02)
        self.assertTrue(seen_active, "Fake CLI should be running before shutdown.")
        code, body = self.call("POST", "/api/shutdown", key=KEY, csrf=self.csrf)
        self.assertEqual(code, 200)
        self.assertTrue(body["stopped"])
        self.assertTrue(self.server.stopping.is_set())
        worker.join(timeout=4)
        self.assertFalse(worker.is_alive(), "Shutdown must cancel in-flight CLI.")
        self.thread.join(timeout=4)
        self.assertFalse(self.thread.is_alive(), "HTTP server must terminate.")


if __name__ == "__main__":
    unittest.main()
