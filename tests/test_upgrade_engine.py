"""The engine upgrade routine pins exact versions and applies only passed trials."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import upgrade_engine as upgrade


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".venv").mkdir()
        (self.root / "requirements.txt").write_text(
            "PyQt6==6.11.0\nPyQt6-WebEngine==6.11.0\nPyQt6-Qt6>=6.11,<6.12\n"
            "# Exact: a comment\nPyQt6-WebEngine-Qt6==6.11.2\nadblock==0.6.0\n"
        )
        for name, value in (
            ("PROJECT", self.root),
            ("TRIAL", self.root / ".venv-upgrade"),
            ("REPORT", self.root / ".venv-upgrade" / "steed-trial.json"),
            ("PREVIOUS", self.root / ".venv" / "steed-previous-engine.json"),
        ):
            item = patch.object(upgrade, name, value)
            item.start()
            self.addCleanup(item.stop)

    def test_versions_compare_numerically(self):
        self.assertGreater(upgrade.key("6.140.0"), upgrade.key("6.12.0"))
        self.assertGreater(upgrade.key("6.12.0"), upgrade.key("6.11.2"))

    def test_pin_replaces_only_the_four_packages(self):
        upgrade._pin(
            {
                "PyQt6": "6.12.0",
                "PyQt6-Qt6": "6.12.0",
                "PyQt6-WebEngine": "6.140.0",
                "PyQt6-WebEngine-Qt6": "6.140.0",
            }
        )
        text = (self.root / "requirements.txt").read_text()
        self.assertEqual(
            text.splitlines(),
            [
                "PyQt6==6.12.0",
                "PyQt6-WebEngine==6.140.0",
                "PyQt6-Qt6==6.12.0",
                "# Exact: a comment",
                "PyQt6-WebEngine-Qt6==6.140.0",
                "adblock==0.6.0",
            ],
        )

    def test_apply_needs_a_passed_trial_and_rollback_restores(self):
        with self.assertRaises(SystemExit):
            upgrade.apply()
        upgrade.REPORT.parent.mkdir()
        versions = dict.fromkeys(upgrade.PACKAGES, "9.9.9")
        upgrade.REPORT.write_text(json.dumps({"passed": False, "versions": versions}))
        with self.assertRaises(SystemExit):
            upgrade.apply()
        upgrade.REPORT.write_text(json.dumps({"passed": True, "versions": versions}))
        before = dict.fromkeys(upgrade.PACKAGES, "1.0.0")
        with (
            patch.object(upgrade, "installed", return_value=before),
            patch.object(upgrade, "_run") as run,
        ):
            upgrade.apply()
            self.assertIn("PyQt6==9.9.9", (self.root / "requirements.txt").read_text())
            upgrade.rollback()
        self.assertIn("PyQt6==1.0.0", (self.root / "requirements.txt").read_text())
        self.assertEqual(run.call_count, 2)
        self.assertFalse(upgrade.PREVIOUS.exists())


if __name__ == "__main__":
    unittest.main()
