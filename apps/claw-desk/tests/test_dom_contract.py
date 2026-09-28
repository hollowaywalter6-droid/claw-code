"""Static UI contract: prevent broken iPhone buttons from missing DOM elements."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

STATIC=Path(__file__).resolve().parents[1]/"static"


class IDParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set()
        self.duplicates=[]
    def handle_starttag(self,tag,attrs):
        for name,value in attrs:
            if name=="id":
                if value in self.ids:
                    self.duplicates.append(value)
                self.ids.add(value)


class StaticAppTests(unittest.TestCase):
    def test_every_literal_js_element_reference_exists(self):
        p=IDParser()
        p.feed((STATIC/"index.html").read_text("utf-8"))
        self.assertFalse(p.duplicates,"HTML contains duplicate element IDs")
        js=(STATIC/"app.js").read_text("utf-8")
        used=set(re.findall(r'\$\("([A-Za-z][A-Za-z0-9-]+)"\)',js))
        missing=used-p.ids
        self.assertEqual(missing,set(),"App JS references missing HTML controls: "+str(sorted(missing)))
        for target in ("panel-chat","panel-agents","panel-integrations","panel-tasks"):
            if target=="panel-chat":
                continue
            self.assertIn(target,p.ids)

    def test_emergency_control_and_voice_attachment_controls_exist(self):
        p=IDParser()
        p.feed((STATIC/"index.html").read_text("utf-8"))
        expected=("kill-switch","voice-button","file-picker","tts-button",
                  "agent-form","task-form","google-connect","unlock-form")
        self.assertTrue(set(expected).issubset(p.ids))


if __name__=="__main__":
    unittest.main()
