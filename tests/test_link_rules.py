"""Tracking rules for links: Steed's list, ClearURLs' rules, or your own."""

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from browser.link_cleaning import clean_link


def names(url):
    return {k for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}


CLEARURLS = {"link_rules": "clearurls"}


class LinkRuleTests(unittest.TestCase):
    def test_steeds_list_is_unchanged_and_cautious(self):
        url = "https://news.example.org/a?id=7&utm_source=x&fbclid=y&si=keep"
        self.assertEqual(clean_link(url), "https://news.example.org/a?id=7&si=keep")
        self.assertEqual(clean_link(url, {"link_rules": "steed"}), clean_link(url))

    def test_clearurls_removes_site_trackers_and_keeps_what_pages_need(self):
        cases = [
            (
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=AbC&feature=share&t=42s",
                {"v", "t"},
            ),
            ("https://open.spotify.com/track/4uLU6?si=abc123", set()),
            ("https://www.reddit.com/r/linux/comments/abc/t/?share_id=x&rdt=1", set()),
            ("https://github.com/mozilla/readability?tab=readme-ov-file", {"tab"}),
            ("https://duckduckgo.com/?q=kettle&ia=web", {"q", "ia"}),
            (
                "https://www.facebook.com/story.php?story_fbid=1&id=2&mibextid=N",
                {"story_fbid", "id"},
            ),
        ]
        for url, kept in cases:
            with self.subTest(url=url):
                self.assertEqual(names(clean_link(url, CLEARURLS)), kept)
        amazon = clean_link(
            "https://www.amazon.com/dp/B09LCJPZ1P/ref=sr_1_3?crid=2X&qid=1&sr=8-3",
            CLEARURLS,
        )
        self.assertEqual(amazon, "https://www.amazon.com/dp/B09LCJPZ1P")

    def test_clearurls_follows_tracking_redirects(self):
        target = "https://example.org/page?id=3"
        redirect = "https://www.google.com/url?q=" + target + "&sa=D&usg=AOv"
        cleaned = clean_link(redirect, CLEARURLS)
        self.assertTrue(cleaned.startswith("https://example.org/page"), cleaned)
        # Copy Clean Link of the target itself is left as it is.
        self.assertEqual(clean_link(target, CLEARURLS), target)

    def test_your_own_rules_file_and_extra_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            rules = Path(folder) / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "providers": {
                            "shop": {
                                "urlPattern": r"^https?://shop\.example\.com",
                                "rules": ["clickref", "aff_[a-z]+"],
                                "exceptions": [r"^https?://shop\.example\.com/keep"],
                            }
                        }
                    }
                )
            )
            own = {"link_rules": "file", "link_rules_file": str(rules)}
            self.assertEqual(
                clean_link("https://shop.example.com/p?id=1&clickref=a&aff_x=b", own),
                "https://shop.example.com/p?id=1",
            )
            self.assertIn(
                "clickref",
                clean_link("https://shop.example.com/keep?clickref=a", own),
            )
            # A missing or broken file removes only what Steed's list does.
            broken = {"link_rules": "file", "link_rules_file": folder + "/none.json"}
            self.assertEqual(
                clean_link("https://shop.example.com/p?clickref=a&utm_x=1", broken),
                "https://shop.example.com/p?clickref=a",
            )
        extra = {"link_rules": "steed", "link_rules_extra": "ref, Source share_id"}
        self.assertEqual(
            clean_link("https://a.example/x?ref=1&source=2&share_id=3&q=4", extra),
            "https://a.example/x?q=4",
        )

    def test_settings_check_the_choice(self):
        from ui.settings_dialog import DEFAULTS, SettingsDialog

        values = dict(DEFAULTS)
        SettingsDialog._validate_values({**values, "link_rules": "clearurls"})
        for bad in ({"link_rules": "everything"}, {"link_rules": "file"}):
            with self.assertRaises(ValueError):
                SettingsDialog._validate_values({**values, **bad})


if __name__ == "__main__":
    unittest.main()
