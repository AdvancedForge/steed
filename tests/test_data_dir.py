"""A second Steed on its own data folder (steed --data-dir FOLDER)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser import config as config_module
from main import take_data_dir


class DataDirTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop(config_module.DATA_ENV, None)
        self.addCleanup(self.restore)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def restore(self):
        os.environ.pop(config_module.DATA_ENV, None)
        if self.saved is not None:
            os.environ[config_module.DATA_ENV] = self.saved

    def test_the_usual_folder_unless_another_is_chosen(self):
        with patch("pathlib.Path.home", return_value=Path(self.folder.name)):
            self.assertEqual(
                config_module.data_dir(), Path(self.folder.name) / ".custom_browser"
            )
            self.assertFalse(config_module.own_data_dir())
        os.environ[config_module.DATA_ENV] = self.folder.name + "/copy"
        self.assertEqual(config_module.data_dir(), Path(self.folder.name, "copy"))
        self.assertTrue(config_module.own_data_dir())
        found = config_module.Config()
        self.assertEqual(found.config_dir, Path(self.folder.name, "copy"))
        self.assertTrue((found.config_dir / "profiles").is_dir())

    def test_the_argument_is_taken_out_and_passed_on(self):
        for arguments in (
            ["steed", "--data-dir", "/tmp/a", "https://example.org"],
            ["steed", "--data-dir=/tmp/a", "https://example.org"],
        ):
            os.environ.pop(config_module.DATA_ENV, None)
            take_data_dir(arguments)
            self.assertEqual(arguments, ["steed", "https://example.org"])
            self.assertEqual(os.environ[config_module.DATA_ENV], "/tmp/a")
        unchanged = ["steed", "https://example.org"]
        take_data_dir(unchanged)
        self.assertEqual(unchanged, ["steed", "https://example.org"])

    def test_agents_using_its_launcher_reach_that_steed(self):
        from services import mcp_launcher

        mcp_launcher.write(self.folder.name, executable="/usr/bin/python3")
        usual = (Path(self.folder.name) / mcp_launcher.NAME).read_text()
        self.assertNotIn("--discovery", usual)
        os.environ[config_module.DATA_ENV] = self.folder.name
        mcp_launcher.write(self.folder.name, executable="/usr/bin/python3")
        own = (Path(self.folder.name) / mcp_launcher.NAME).read_text()
        self.assertIn(f"--discovery {self.folder.name}/agent-access.json", own)


if __name__ == "__main__":
    unittest.main()
