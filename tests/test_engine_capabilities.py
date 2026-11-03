"""Engine upgrades must never confer website-script authority by themselves."""

import unittest
from unittest.mock import patch

from services.engine_capabilities import qt_engine_capabilities
from services.task_policy import SUPPORTED_ACTIONS, TaskScope


class EngineCapabilitiesTests(unittest.TestCase):
    def report(self, version):
        with patch(
            "webengine.versions",
            return_value={"qt": "6.11.2", "chromium": version, "security": version},
        ):
            return qt_engine_capabilities()

    def test_bundled_engine_needs_upgrade(self):
        report = self.report("140.0.7339.225")
        self.assertFalse(report.native_policy_candidate)
        self.assertFalse(report.website_scripts)
        self.assertEqual(report.script_unavailable_reason, "engine_upgrade_required")
        self.assertEqual(set(report.supported_actions), SUPPORTED_ACTIONS)

    def test_newer_engine_is_only_a_candidate_and_cannot_grant_scripts(self):
        for version in ("152.0.0.0", "153.0.8010.36"):
            with self.subTest(version=version):
                report = self.report(version)
                self.assertTrue(report.native_policy_candidate)
                self.assertFalse(report.website_scripts)
                self.assertNotIn("scripts", report.supported_actions)
                self.assertEqual(
                    report.script_unavailable_reason, "native_policy_not_integrated"
                )
                with self.assertRaises(ValueError):
                    TaskScope.parse(
                        {"origins": ["https://example.org"], "actions": ["scripts"]}
                    )

    def test_unverified_version_is_not_a_native_policy_candidate(self):
        for version in ("", "153", "153.0.8010.36-dev", "unknown"):
            with self.subTest(version=version):
                report = self.report(version)
                self.assertFalse(report.native_policy_candidate)
                self.assertFalse(report.website_scripts)
                self.assertEqual(
                    report.script_unavailable_reason, "engine_version_unverified"
                )
