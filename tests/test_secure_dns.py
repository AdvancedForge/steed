"""Secure DNS: Settings choose the engine's DNS mode, applied at once."""

import unittest
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtWebEngineCore import QWebEngineGlobalSettings as Settings
from qt_test_support import application

from browser import secure_dns, webengine


class SecureDnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def applied(self, config):
        """What apply() hands Qt's setDnsMode, read back from the C++ object."""
        seen = []

        def fake(address):
            choice = sip.wrapinstance(address, Settings.DnsMode)
            seen.append((choice.secureMode, list(choice.serverTemplates)))
            return True

        with patch.object(webengine, "_dns_function", return_value=fake):
            self.assertTrue(secure_dns.apply(config))
        return seen[0]

    def test_modes_map_to_the_engine(self):
        server = "https://dns.quad9.net/dns-query"
        self.assertEqual(
            self.applied({"secure_dns": "off", "secure_dns_server": server}),
            (Settings.SecureDnsMode.SystemOnly, []),
        )
        self.assertEqual(
            self.applied({"secure_dns": "automatic", "secure_dns_server": server}),
            (Settings.SecureDnsMode.SecureWithFallback, [server]),
        )
        self.assertEqual(
            self.applied({"secure_dns": "strict", "secure_dns_server": server}),
            (Settings.SecureDnsMode.SecureOnly, [server]),
        )

    def test_a_bad_saved_value_falls_back_to_the_system(self):
        self.assertEqual(
            self.applied({"secure_dns": "strict", "secure_dns_server": "http://x"}),
            (Settings.SecureDnsMode.SystemOnly, []),
        )

    def test_the_real_function_is_found(self):
        self.assertTrue(callable(webengine._dns_function()))

    def test_settings_refuse_bad_choices(self):
        from ui.settings_dialog import DEFAULTS, SettingsDialog

        values = dict(DEFAULTS)
        SettingsDialog._validate_values(values)
        for mode, server in (("sometimes", ""), ("strict", "dns.example")):
            with self.assertRaises(ValueError):
                SettingsDialog._validate_values(
                    {**values, "secure_dns": mode, "secure_dns_server": server}
                )
        # Off ignores the server.
        SettingsDialog._validate_values({**values, "secure_dns_server": ""})


if __name__ == "__main__":
    unittest.main()
