"""Freed heap is handed back once, shortly after a burst of closes."""

import time
import unittest
from unittest.mock import Mock, patch

from qt_test_support import WAIT_SECONDS, application

import memory_trim


class MemoryTrimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_bursts_of_closes_trim_once_after_the_delay(self):
        trim = Mock()
        with (
            patch.object(memory_trim, "_trim", trim),
            patch.object(memory_trim, "DELAY_MS", 50),
            patch.object(memory_trim, "_timer", None),
        ):
            for _ in range(5):
                memory_trim.schedule()
            trim.assert_not_called()
            end = time.monotonic() + WAIT_SECONDS
            while time.monotonic() < end and not trim.called:
                self.app.processEvents()
                time.sleep(0.01)
            trim.assert_called_once_with(0)

    def test_without_glibc_nothing_is_scheduled(self):
        with (
            patch.object(memory_trim, "_trim", False),
            patch.object(memory_trim, "_timer", None),
        ):
            memory_trim.schedule()
            self.assertIsNone(memory_trim._timer)
            memory_trim.release_now()

    def test_real_trim_is_callable(self):
        memory_trim.release_now()


if __name__ == "__main__":
    unittest.main()
