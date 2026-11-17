"""The local translation service script, without installing anything."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser import setup_translation as setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "svc"

    def test_not_installed_reports_and_does_not_start(self):
        self.assertEqual(setup.status(self.home), 1)
        self.assertEqual(setup.start(self.home), 1)
        self.assertIsNone(setup.running(self.home))
        self.assertEqual(setup.stop(self.home), 0)

    def test_install_uses_its_own_environment_and_points_steed_at_it(self):
        calls = []
        with (
            patch.object(
                setup.subprocess, "run", side_effect=lambda c, **k: calls.append(c)
            ),
            patch.object(setup, "set_steed_service") as service,
        ):
            self.assertEqual(setup.install(self.home, "en,de"), 0)
        self.assertTrue(any("libretranslate" in c for c in calls))
        self.assertTrue(any(setup.CPU_WHEELS in c for c in calls))
        self.assertTrue(
            all(
                str(self.home) in c[0]
                or c[0].endswith("python3")
                or "venv" in " ".join(c)
                for c in calls
            )
        )
        service.assert_called_once_with(setup.SERVICE)
        self.assertEqual(setup.paths(self.home)["settings"].read_text(), "en,de")
        self.assertTrue(setup.SERVICE.startswith("http://127.0.0.1:"))

    def test_status_reports_languages_and_uninstall_clears_steed(self):
        server = setup.paths(self.home)["server"]
        server.parent.mkdir(parents=True)
        server.write_text("")
        with patch.object(
            setup, "answering", return_value=[{"code": "de"}, {"code": "en"}]
        ):
            self.assertEqual(setup.status(self.home), 0)
        with patch.object(setup, "set_steed_service") as service:
            setup.uninstall(self.home)
        service.assert_called_once_with("")
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
