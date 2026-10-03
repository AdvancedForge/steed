"""Packaged data must retain the source-review and account consent boundaries."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import test_recipe_proposals as fixtures
from recipe_support import recipe

from services.bundled_recipes import MAX_BUNDLED_RECIPES, BundledRecipes
from services.connector_recipes import MAX_RECIPE_BYTES, RecipeError
from ui.app_connections import AppConnectionsDialog
from ui.bundled_recipes import BundledRecipesDialog


class BundledRecipeFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.catalog = BundledRecipes(self.root)

    def tearDown(self):
        self.directory.cleanup()

    def test_missing_directory_and_invalid_entries_are_empty(self):
        self.assertEqual(BundledRecipes(self.root / "missing").list(), ())
        (self.root / "broken.json").write_bytes(b"{")
        (self.root / "large.json").write_bytes(b" " * (MAX_RECIPE_BYTES + 1))
        (self.root / "directory.json").mkdir()
        (self.root / "link.json").symlink_to(self.root / "broken.json")
        os.mkfifo(self.root / "pipe.json")
        (self.root / "Recipe.json").write_bytes(recipe().raw)
        self.assertEqual(self.catalog.list(), ())
        for name in ("../outside", "one/two", "https://service", "UPPER", ""):
            with self.subTest(name=name), self.assertRaises(RecipeError):
                self.catalog.read(name)

    def test_changed_source_cannot_use_an_old_selection(self):
        source = self.root / "notes.json"
        source.write_bytes(recipe().raw)
        entry = self.catalog.list()[0]
        self.assertEqual(self.catalog.select(entry), recipe())
        data = json.loads(recipe().raw)
        data["name"] = "Changed Notes"
        source.write_text(json.dumps(data))
        with self.assertRaises(RecipeError):
            self.catalog.select(entry)
        source.unlink()
        with self.assertRaises(OSError):
            self.catalog.select(entry)

    def test_discovery_is_sorted_and_bounded(self):
        for i in range(MAX_BUNDLED_RECIPES + 5):
            (self.root / f"service_{i:02}.json").write_bytes(recipe().raw)
        entries = self.catalog.list()
        self.assertEqual(len(entries), MAX_BUNDLED_RECIPES)
        self.assertEqual(entries[0].slug, "service_00")
        self.assertEqual(entries[-1].slug, "service_49")


class BundledRecipeDialogTests(unittest.TestCase):
    setUpClass = fixtures.RecipeProposalTests.__dict__["setUpClass"]
    tearDown = fixtures.RecipeProposalTests.tearDown
    wait_for = fixtures.RecipeProposalTests.wait_for
    task = fixtures.RecipeProposalTests.task

    def setUp(self):
        fixtures.RecipeProposalTests.setUp(self)
        self.packaged = self.root / "packaged"
        self.packaged.mkdir()
        self.manager.bundled_recipes = BundledRecipes(self.packaged)

    def test_empty_picker_and_connections_entry(self):
        dialog = AppConnectionsDialog(self.manager)
        self.dialogs.append(dialog)
        dialog.bundled_recipe_button.click()
        picker = dialog.bundled_dialog
        self.assertFalse(picker.review_button.isEnabled())
        self.assertIn("No packaged connectors", picker.description.text())
        self.assertEqual(self.http.calls, [])
        self.assertEqual(self.accounts.list(), ())

    def test_selection_requires_exact_default_deny_source_review(self):
        (self.packaged / "notes.json").write_bytes(recipe().raw)
        picker = BundledRecipesDialog(self.manager)
        self.dialogs.append(picker)
        picker.show()
        self.app.processEvents()
        picker.grab().save("/tmp/steed-bundled-recipes-preview.png")
        picker.review_button.click()
        self.assertTrue(picker.review_dialog.deny_button.isDefault())
        self.assertEqual(self.manager.providers, {})
        self.assertEqual(self.http.calls, [])
        picker.review_dialog.deny_button.click()
        self.assertEqual(self.manager.providers, {})
        picker.review_button.click()
        picker.review_dialog.allow_button.click()
        self.assertIn(recipe().provider, self.manager.providers)
        self.assertEqual(self.accounts.list(), ())
        self.assertEqual(self.http.calls, [])

    def test_changed_recipe_requires_reopening_picker(self):
        path = self.packaged / "notes.json"
        path.write_bytes(recipe().raw)
        picker = BundledRecipesDialog(self.manager)
        self.dialogs.append(picker)
        path.write_bytes(b"invalid source")
        picker.review_button.click()
        self.assertFalse(hasattr(picker, "review_dialog"))
        self.assertIn("unavailable or changed", picker.status.text())
        self.assertEqual(self.manager.providers, {})
