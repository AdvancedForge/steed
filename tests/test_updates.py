"""Update checks: new Steed changes on the git remote, a newer engine on PyPI
(a local stand-in here), shown on steed:features; nothing is changed."""

import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from qt_test_support import WAIT_SECONDS, application

from services import updates


class PyPI(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "releases": {
                    "6.11.2": [{"x": 1}],
                    "6.14.0": [{"x": 1}],
                    "6.15.0rc1": [{"x": 1}],
                    "6.16.0": [],
                }
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def git(folder, *arguments):
    subprocess.run(
        ["git", "-C", str(folder), *arguments],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.org",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.org",
            "HOME": str(folder),
            "PATH": "/usr/bin:/bin",
        },
    )


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), PyPI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def test_newest_stable_release(self):
        self.assertEqual(
            updates.newest_stable({"6.9.0": [1], "6.10.1": [1], "7.0b1": [1], "8": []}),
            "6.10.1",
        )

    def test_a_check_reports_new_changes_and_a_newer_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, work, mine = root / "remote.git", root / "work", root / "mine"
            subprocess.run(
                ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
            )
            subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
            (work / "a").write_text("1")
            git(work, "add", "a")
            git(work, "commit", "-qm", "one")
            git(work, "push", "-q", "origin", "HEAD:main")
            subprocess.run(["git", "clone", "-q", str(remote), str(mine)], check=True)
            for n in (2, 3):
                (work / "a").write_text(str(n))
                git(work, "commit", "-qam", f"change {n}")
            git(work, "push", "-q", "origin", "HEAD:main")
            config = {}

            class Config(dict):
                def set(self, key, value):
                    self[key] = value

            config = Config()
            pypi = f"http://127.0.0.1:{self.server.server_port}/{{name}}"
            with (
                patch.object(updates, "PYPI", pypi),
                patch.object(
                    updates.Updates, "_installed_engine", return_value="6.11.2"
                ),
            ):
                service = updates.Updates(config, project=mine)
                try:
                    service.check()
                    self.wait(
                        lambda: (
                            service.state["steed"] is not None
                            and service.state["engine"] is not None
                        )
                    )
                    self.assertEqual(service.state["steed"], 2)
                    self.assertEqual(service.state["engine"], "6.14.0")
                    summary = service.summary()
                    self.assertIn("2 changes", summary)
                    self.assertIn("Qt WebEngine 6.14.0", summary)
                    # The status bar says it plainly, with no commands to run.
                    notice = service.notice()
                    self.assertIn("A newer Steed and a web engine", notice)
                    self.assertNotIn("python", notice)
                    self.assertNotIn(".sh", notice)
                    # Nothing in the checkout changed.
                    self.assertEqual((mine / "a").read_text(), "1")
                    # Checked: not again within a day; never when switched off.
                    with patch.object(service, "check") as again:
                        service.check_if_due()
                        again.assert_not_called()
                        config["updates_checked"] = 0
                        config["check_updates"] = False
                        service.check_if_due()
                        again.assert_not_called()
                finally:
                    service.close()


    def test_a_copy_that_is_not_a_git_checkout_says_so(self):
        class Config(dict):
            def set(self, key, value):
                self[key] = value

        with tempfile.TemporaryDirectory() as directory:
            pypi = f"http://127.0.0.1:{self.server.server_port}/{{name}}"
            with patch.object(updates, "PYPI", pypi):
                service = updates.Updates(Config(), project=Path(directory))
                try:
                    service.check()
                    self.wait(lambda: service.state["engine"] is not None)
                    self.assertFalse(service.state["steed_checked"])
                    self.assertIsNone(service.state["steed"])
                finally:
                    service.close()


if __name__ == "__main__":
    unittest.main()
