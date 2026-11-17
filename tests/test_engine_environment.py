"""Engine environment variables are defined before Qt starts any thread."""

import os
import unittest
from unittest.mock import patch

import qt_test_support

from browser.webengine import ENGINE_ENVIRONMENT, prepare_engine_environment


class EngineEnvironmentTests(unittest.TestCase):
    def test_missing_variables_are_defined_and_user_values_kept(self):
        with patch.dict(os.environ, {"LIBVA_MESSAGING_LEVEL": "2"}, clear=False):
            os.environ.pop("force_s3tc_enable", None)
            prepare_engine_environment()
            self.assertEqual(os.environ["LIBVA_MESSAGING_LEVEL"], "2")
            self.assertEqual(os.environ["force_s3tc_enable"], "true")

    def test_application_startup_prepares_the_environment(self):
        qt_test_support.application()
        for name in ENGINE_ENVIRONMENT:
            self.assertIn(name, os.environ)


if __name__ == "__main__":
    unittest.main()
