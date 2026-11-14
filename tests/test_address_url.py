"""What typing in the address bar opens: a page, a local server over http,
Steed's own pages, or a search."""

import unittest

from PyQt6.QtCore import QUrl

from services.navigation_service import NavigationService, local_address

SEARCH = "https://search.example/?q="


class AddressTests(unittest.TestCase):
    def opens(self, text):
        return bytes(NavigationService.address_url(text, SEARCH).toEncoded()).decode()

    def test_addresses_open_over_https(self):
        for text, expected in (
            ("example.com", "https://example.com"),
            ("example.com/path?x=1", "https://example.com/path?x=1"),
            ("  sub.example.org  ", "https://sub.example.org"),
            ("http://example.com", "http://example.com"),
            ("https://example.com/a b", "https://example.com/a%20b"),
            ("8.8.8.8", "https://8.8.8.8"),
        ):
            self.assertEqual(self.opens(text), expected, text)

    def test_this_computer_and_the_local_network_use_http(self):
        for text in (
            "localhost",
            "localhost:8000",
            "localhost:8000/app",
            "127.0.0.1:5000",
            "[::1]:8080",
            "192.168.1.5:8080",
            "10.0.0.2",
            "172.16.4.1/admin",
            "printer.local",
            "router.lan/setup",
            "app.localhost:3000",
        ):
            self.assertTrue(self.opens(text).startswith("http://"), text)
        self.assertFalse(local_address("172.32.0.1"))  # Public range.
        self.assertFalse(local_address("localhost.example.com"))

    def test_words_and_sentences_are_searched(self):
        for text, query in (
            ("weather", "weather"),
            ("what is python", "what%20is%20python"),
            ("Mr. Smith goes", "Mr.%20Smith%20goes"),
            ("1 + 1 = 2", "1%20%2B%201%20%3D%202"),
            ("", ""),
        ):
            self.assertEqual(self.opens(text), SEARCH + query, text)

    def test_steed_and_about_pages_pass_through(self):
        self.assertEqual(self.opens("about:blank"), "about:blank")
        self.assertEqual(QUrl(self.opens("steed:newtab")).scheme(), "steed")
        self.assertEqual(QUrl(self.opens("steed:settings")).scheme(), "steed")


if __name__ == "__main__":
    unittest.main()
