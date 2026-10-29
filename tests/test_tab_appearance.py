"""Tab strip settings are bounded presentation data in stored and portable layouts."""

import json
import unittest
from copy import deepcopy

from layout_format import export_layout, layout_changes, parse_layout
from layout_preferences import LayoutPreferences
from tab_appearance import clean_tabs, default_tabs, validate_tabs


class TabAppearanceTests(unittest.TestCase):
    def test_invalid_stored_values_fall_back_one_field_at_a_time(self):
        cleaned = clean_tabs(
            {
                "position": "left",
                "style": "<script>",
                "favicons": "yes",
                "tab_count": True,
                "vertical_width": 9999,
                "unknown": "kept?",
            }
        )
        self.assertEqual(cleaned["position"], "left")
        self.assertEqual(cleaned["style"], "rounded")
        self.assertIs(cleaned["favicons"], True)
        self.assertIs(cleaned["tab_count"], True)
        self.assertEqual(cleaned["vertical_width"], 480)
        self.assertNotIn("unknown", cleaned)
        self.assertEqual(clean_tabs(None), default_tabs())

    def test_preferences_keep_partial_edits_and_older_documents_get_defaults(self):
        preferences = LayoutPreferences({"version": 2, "layout": {}})
        self.assertEqual(preferences.tabs, default_tabs())
        preferences.set_tabs({"position": "right", "style": "pill"})
        preferences.set_tabs({"overflow": "wrap"})
        tabs = preferences.tabs
        self.assertEqual(
            (tabs["position"], tabs["style"], tabs["overflow"]),
            ("right", "pill", "wrap"),
        )
        restored = LayoutPreferences(preferences.to_dict())
        self.assertEqual(restored.tabs, tabs)
        self.assertEqual(restored.to_dict()["version"], 2)

    def test_portable_v3_is_exact_and_v2_documents_still_import(self):
        preferences = LayoutPreferences()
        preferences.set_tabs({"position": "left", "grouping": "site"})
        source = export_layout(preferences, "Side tabs")
        document = json.loads(source)
        self.assertEqual(document["version"], 3)
        definition = parse_layout(source)
        self.assertEqual(definition.appearance["tabs"]["position"], "left")
        for mutation in (
            lambda d: d["layout"]["tabs"].update(position="diagonal"),
            lambda d: d["layout"]["tabs"].update(script="alert(1)"),
            lambda d: d["layout"]["tabs"].update(vertical_width=50),
            lambda d: d["layout"]["tabs"].update(favicons=1),
            lambda d: d["layout"].pop("tabs"),
        ):
            changed = deepcopy(document)
            mutation(changed)
            with self.assertRaises(ValueError):
                parse_layout(json.dumps(changed))
        older = deepcopy(document)
        older["version"] = 2
        del older["layout"]["tabs"]
        self.assertEqual(
            parse_layout(json.dumps(older)).appearance["tabs"], default_tabs()
        )
        with self.assertRaises(ValueError):
            older["layout"]["tabs"] = default_tabs()
            parse_layout(json.dumps(older))

    def test_review_describes_tab_changes(self):
        before = LayoutPreferences()
        after = before.copy()
        after.set_tabs({"position": "bottom", "new_tab_button": "end"})
        changes = layout_changes(before, after)
        self.assertIn("Tab position: bottom", changes)
        self.assertIn("New tab button: end", changes)
        self.assertEqual(validate_tabs(after.tabs)["position"], "bottom")


if __name__ == "__main__":
    unittest.main()
