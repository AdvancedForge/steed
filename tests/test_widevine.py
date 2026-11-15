"""Protected streaming: Steed finds a Widevine module it may use, never ships one."""

import tempfile
import unittest
from pathlib import Path

import widevine


class WidevineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def module(self, relative):
        path = self.home / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\\x7fELF")
        return path

    def test_nothing_installed(self):
        self.assertEqual(widevine.find(self.home), {"path": None, "source": ""})
        self.assertEqual(widevine.flags(self.home), [])

    def test_firefox_module_is_handed_to_the_engine(self):
        self.module(
            "snap/firefox/common/.mozilla/firefox/a.default/gmp-widevinecdm/4.10.2.0/libwidevinecdm.so"
        )
        newest = self.module(
            ".mozilla/firefox/b.default/gmp-widevinecdm/4.10.10.0/libwidevinecdm.so"
        )
        self.assertEqual(widevine.find(self.home)["source"], "firefox")
        self.assertEqual(widevine.flags(self.home), [f"--widevine-path={newest}"])

    def test_chromes_module_is_found_by_the_engine_itself(self):
        self.module(
            ".mozilla/firefox/b.default/gmp-widevinecdm/4.10.10.0/libwidevinecdm.so"
        )
        self.module(
            ".config/chromium/WidevineCdm/4.10.2934.0/_platform_specific/linux_x64/libwidevinecdm.so"
        )
        self.assertEqual(widevine.find(self.home)["source"], "chrome")
        self.assertEqual(widevine.flags(self.home), [])  # No flag needed.


if __name__ == "__main__":
    unittest.main()
