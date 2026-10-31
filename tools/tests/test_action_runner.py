import io
import json
import os
import shutil
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import action_runner
from recipebot import telegram


class FakeHTTPResponse:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


class TelegramTests(unittest.TestCase):
    def test_send_message(self):
        seen = {}

        def mock_opener(req, timeout):
            seen["url"] = req.full_url
            seen["data"] = json.loads(req.data.decode("utf-8"))
            return FakeHTTPResponse(b'{"ok": true}')

        resp = telegram.send_message("123:TOKEN", 456, "<b>Hello</b>", reply_to_message_id=789, opener=mock_opener)
        self.assertTrue(resp.get("ok"))
        self.assertEqual(seen["url"], "https://api.telegram.org/bot123:TOKEN/sendMessage")
        self.assertEqual(seen["data"]["chat_id"], 456)
        self.assertEqual(seen["data"]["text"], "<b>Hello</b>")
        self.assertEqual(seen["data"]["reply_to_message_id"], 789)
        self.assertEqual(seen["data"]["parse_mode"], "HTML")

    def test_download_file(self):
        def mock_opener(req, timeout):
            if "getFile" in req.full_url:
                return FakeHTTPResponse(b'{"ok": true, "result": {"file_path": "photos/test.jpg"}}')
            return FakeHTTPResponse(b"image_bytes_here")

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            p = telegram.download_file("123:TOKEN", "fid123", dest, opener=mock_opener)
            self.assertTrue(p.exists())
            self.assertEqual(p.name, "fid123.jpg")
            self.assertEqual(p.read_bytes(), b"image_bytes_here")


class ActionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_root = action_runner.REPO_ROOT
        action_runner.REPO_ROOT = self.tmp

    def tearDown(self):
        action_runner.REPO_ROOT = self.orig_root
        shutil.rmtree(self.tmp)

    @patch("subprocess.run")
    def test_git_undo_reverts_bot_commit(self, mock_run):
        mock_run.return_value = MagicMock(stdout="Add Almás pite recipe (bot)\n")
        ok, msg = action_runner.git_undo(self.tmp)
        self.assertTrue(ok)
        self.assertIn("Visszavonva", msg)

    @patch("subprocess.run")
    def test_git_undo_refuses_manual_commit(self, mock_run):
        mock_run.return_value = MagicMock(stdout="Manual edit by author\n")
        ok, msg = action_runner.git_undo(self.tmp)
        self.assertFalse(ok)
        self.assertIn("nem a bot által készült", msg)


if __name__ == "__main__":
    unittest.main()
