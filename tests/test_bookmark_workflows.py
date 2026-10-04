"""Profile bookmark organization, inert HTML interoperability and atomic imports."""

import unittest
from unittest.mock import patch

import test_start_pages as pages_fixture
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QPushButton

from bookmark_format import (
    MAX_BOOKMARK_HTML_BYTES,
    MAX_BOOKMARKS,
    export_bookmarks,
    parse_bookmarks,
)
from services.start_pages import web_url
from ui.bookmarks_dialog import BookmarkDialog, BookmarksDialog

SAMPLE = b"""<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>
<DT><H3>Work</H3><DL><p><DT><A HREF="https://one.example/">One &amp; two</A>
<DT><H3>Research</H3><DL><p><DT><A HREF="https://two.example/">Two</A></DL><p></DL><p>
<DT><A HREF="javascript:alert(1)">Unsafe</A>
<DT><A HREF="https://user:secret@example.org/">Credentials</A>
<DT><A HREF="https://one.example/">Duplicate</A>
</DL><p>"""


class BookmarkFormatTests(unittest.TestCase):
    def test_nested_folders_entities_and_invalid_urls_are_plain_data(self):
        entries, skipped = parse_bookmarks(SAMPLE, web_url)
        self.assertEqual(skipped, 2)
        self.assertEqual(
            entries[0],
            {"url": "https://one.example/", "title": "One & two", "folder": "Work"},
        )
        self.assertEqual(entries[1]["folder"], "Work / Research")
        self.assertEqual(entries[2]["folder"], "")

    def test_export_roundtrip_escapes_titles_folders_and_addresses(self):
        items = [
            {
                "url": "https://example.org/?a=1&b=2",
                "title": "<script> & literal",
                "folder": "Work & <notes>",
            }
        ]
        content = export_bookmarks(items)
        self.assertIn(b"&lt;script&gt;", content)
        self.assertNotIn(b"<script>", content)
        self.assertEqual(parse_bookmarks(content, web_url), (items, 0))

    def test_import_bounds_and_malformed_input(self):
        for content in (
            b"",
            b"\xff",
            b"<p>No bookmarks</p>",
            b" " * (MAX_BOOKMARK_HTML_BYTES + 1),
            b"<DL>" * 17,
        ):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                parse_bookmarks(content, web_url)
        content = (
            b"<DL>"
            + b'<A HREF="https://example.org/">One</A>' * (MAX_BOOKMARKS + 1)
            + b"</DL>"
        )
        with self.assertRaises(ValueError):
            parse_bookmarks(content, web_url)


class BookmarkWorkflowTests(unittest.TestCase):
    setUpClass = classmethod(pages_fixture.StartPageTests.setUpClass.__func__)
    setUp = pages_fixture.StartPageTests.setUp
    tearDown = pages_fixture.StartPageTests.tearDown
    wait = pages_fixture.StartPageTests.wait
    js = pages_fixture.StartPageTests.js
    loaded = pages_fixture.StartPageTests.loaded

    def test_import_preserves_existing_merges_duplicates_and_is_profile_local(self):
        self.pages.add_bookmark(
            "global", "https://one.example/", "My original", folder="Personal"
        )
        original = self.pages.state("global")["bookmarks"][0]
        result = self.pages.import_bookmarks("global", SAMPLE)
        self.assertEqual(result, {"added": 1, "duplicates": 2, "skipped": 2})
        self.assertEqual(self.pages.state("global")["bookmarks"][0], original)
        self.assertEqual(
            self.pages.state("global")["bookmarks"][1]["folder"], "Work / Research"
        )
        self.assertEqual(self.pages.state("other")["bookmarks"], [])
        self.pages._states.clear()
        self.assertEqual(self.pages.state("global")["bookmarks"][0], original)
        self.assertEqual(
            parse_bookmarks(self.pages.export_bookmarks("global"), web_url)[0][1][
                "folder"
            ],
            "Work / Research",
        )

    def test_import_write_failure_and_limit_leave_cache_and_disk_unchanged(self):
        self.pages.add_bookmark("global", "https://existing.example/", "Existing")
        before = self.pages.state("global")
        disk = self.pages.path("global").read_bytes()
        with patch("services.start_pages.save_json", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.pages.import_bookmarks("global", SAMPLE)
        self.assertEqual(self.pages.state("global"), before)
        self.assertEqual(self.pages.path("global").read_bytes(), disk)
        with patch("services.start_pages.MAX_BOOKMARKS", 1):
            with self.assertRaises(ValueError):
                self.pages.import_bookmarks("global", SAMPLE)
        self.assertEqual(self.pages.state("global"), before)
        self.assertEqual(self.pages.path("global").read_bytes(), disk)

    def test_edit_keeps_identity_and_rejects_duplicate_addresses(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        self.pages.add_bookmark("global", "https://two.example/", "Two")
        identity = self.pages.state("global")["bookmarks"][0]["id"]
        self.pages.update_bookmark(
            "global", identity, "https://edited.example/", "Edited", "Work"
        )
        item = self.pages.state("global")["bookmarks"][0]
        self.assertEqual(
            item,
            {
                "id": identity,
                "url": "https://edited.example/",
                "title": "Edited",
                "folder": "Work",
            },
        )
        with self.assertRaises(ValueError):
            self.pages.update_bookmark(
                "global", identity, "https://two.example/", "Collision", ""
            )
        self.assertEqual(self.pages.state("global")["bookmarks"][0], item)
        self.pages.add_bookmark("global", "https://edited.example/", "Renamed")
        self.assertEqual(self.pages.state("global")["bookmarks"][0]["folder"], "Work")

    def test_native_search_folder_and_filtered_remove_target_stable_id(self):
        self.pages.add_bookmark("global", "https://one.example/", "One", folder="Work")
        self.pages.add_bookmark(
            "global", "https://two.example/", "Two", folder="Personal"
        )
        dialog = BookmarksDialog(self.pages, "global")
        try:
            dialog.search.setText("TWO.EXAMPLE")
            self.assertEqual(dialog.items.count(), 1)
            dialog.items.setCurrentRow(0)
            self.assertEqual(dialog.selected()["title"], "Two")
            dialog.remove()
            self.assertEqual(
                [b["title"] for b in self.pages.state("global")["bookmarks"]], ["One"]
            )
            dialog.search.clear()
            dialog.folder.setCurrentIndex(dialog.folder.findData("Work"))
            self.assertEqual(dialog.items.count(), 1)
            self.pages.add_bookmark(
                "global", "https://three.example/", "Three", folder="Work"
            )
            self.assertEqual(dialog.items.count(), 2)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_native_edit_updates_url_folder_and_keeps_other_profile_untouched(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        item = self.pages.state("global")["bookmarks"][0]
        dialog = BookmarkDialog(
            self.pages,
            "global",
            url=item["url"],
            title=item["title"],
            identity=item["id"],
        )
        try:
            self.assertFalse(dialog.url.isReadOnly())
            dialog.url.setText("https://edited.example/")
            dialog.folder.setCurrentText("Research")
            dialog.save()
            self.assertEqual(
                self.pages.state("global")["bookmarks"][0]["folder"], "Research"
            )
            self.assertEqual(self.pages.state("other")["bookmarks"], [])
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_native_import_and_export_cancel_and_failure_do_not_replace_files(self):
        from pathlib import Path

        source = Path(self.directory.name) / "bookmarks.html"
        target = Path(self.directory.name) / "export.html"
        source.write_bytes(SAMPLE)
        dialog = BookmarksDialog(self.pages, "global")
        try:
            with patch(
                "ui.bookmarks_dialog.QFileDialog.getOpenFileName",
                return_value=(str(source), ""),
            ):
                dialog.import_html()
            self.assertIn("Imported 2", dialog.status.text())
            with patch(
                "ui.bookmarks_dialog.QFileDialog.getSaveFileName",
                return_value=(str(target), ""),
            ):
                dialog.export_html()
            self.assertEqual(parse_bookmarks(target.read_bytes(), web_url)[1], 0)
            before = target.read_bytes()
            with (
                patch(
                    "ui.bookmarks_dialog.QFileDialog.getSaveFileName",
                    return_value=(str(target), ""),
                ),
                patch.object(
                    self.pages, "export_bookmarks", side_effect=ValueError("synthetic")
                ),
            ):
                dialog.export_html()
            self.assertEqual(target.read_bytes(), before)
            with patch(
                "ui.bookmarks_dialog.QFileDialog.getOpenFileName", return_value=("", "")
            ):
                dialog.import_html()
            self.assertEqual(len(self.pages.state("global")["bookmarks"]), 2)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_native_manager_opens_filtered_bookmark_in_a_new_tab(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        self.pages.add_bookmark("global", "https://two.example/", "Two")

        def open_filtered(dialog):
            dialog.search.setText("Two")
            dialog.items.setCurrentRow(0)
            button = next(
                b for b in dialog.findChildren(QPushButton) if b.text() == "Open"
            )
            button.click()

        with (
            patch.object(BookmarksDialog, "exec", new=open_filtered),
            patch.object(self.window, "_new_tab") as opened,
        ):
            self.window._manage_bookmarks()
        opened.assert_called_once_with(url="https://two.example/")

    def test_enter_in_search_opens_first_match_without_selecting_a_row(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        self.pages.add_bookmark("global", "https://two.example/", "Two")

        def search_and_open(dialog):
            dialog.show()
            dialog.search.setFocus()
            dialog.search.setText("two")
            self.app.processEvents()
            QTest.keyClick(dialog.search, Qt.Key.Key_Return)
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

        with (
            patch.object(BookmarksDialog, "exec", new=search_and_open),
            patch.object(self.window, "_new_tab") as opened,
        ):
            self.window._manage_bookmarks()
        opened.assert_called_once_with(url="https://two.example/")

    def test_arrow_keys_and_enter_open_selected_result_once(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        self.pages.add_bookmark("global", "https://two.example/", "Two")
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            with self.subTest(key=key):

                def choose_and_open(dialog, key=key):
                    dialog.show()
                    dialog.search.setText(".example")
                    dialog.items.setCurrentRow(0)
                    dialog.items.setFocus()
                    self.app.processEvents()
                    QTest.keyClick(dialog.items, Qt.Key.Key_Down)
                    self.assertEqual(dialog.selected()["title"], "Two")
                    QTest.keyClick(dialog.items, key)
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

                with (
                    patch.object(BookmarksDialog, "exec", new=choose_and_open),
                    patch.object(self.window, "_new_tab") as opened,
                ):
                    self.window._manage_bookmarks()
                opened.assert_called_once_with(url="https://two.example/")

    def test_enter_with_no_search_matches_keeps_manager_open(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        opened = []
        dialog = BookmarksDialog(self.pages, "global", open_url=opened.append)
        try:
            dialog.show()
            dialog.search.setFocus()
            dialog.search.setText("no matching bookmark")
            self.app.processEvents()
            self.assertEqual(dialog.items.count(), 0)
            QTest.keyClick(dialog.search, Qt.Key.Key_Return)
            self.assertTrue(dialog.isVisible())
            self.assertEqual(opened, [])
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_double_click_opens_the_selected_bookmark(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")

        def double_click(dialog):
            dialog.items.setCurrentRow(0)
            dialog.items.itemDoubleClicked.emit(dialog.items.currentItem())

        with (
            patch.object(BookmarksDialog, "exec", new=double_click),
            patch.object(self.window, "_new_tab") as opened,
        ):
            self.window._manage_bookmarks()
        opened.assert_called_once_with(url="https://one.example/")

    def test_manager_does_not_open_after_the_browser_changes_profile(self):
        self.pages.add_bookmark("global", "https://one.example/", "One")
        self.window.profile_manager.create_profile("work")

        def change_profile(dialog):
            dialog.items.setCurrentRow(0)
            self.window._switch_to_profile("work")
            dialog.open_selected()
            self.assertIn("current profile", dialog.status.text())

        with (
            patch.object(BookmarksDialog, "exec", new=change_profile),
            patch.object(self.window, "_new_tab") as opened,
        ):
            self.window._manage_bookmarks()
        # Profile switching may itself create its initial tab. The stale bookmark
        # must never cause an HTTP navigation/new tab in that different profile.
        self.assertFalse(
            any(
                call.kwargs.get("url") == "https://one.example/"
                for call in opened.call_args_list
            )
        )

    def test_private_bookmarks_import_is_memory_only(self):
        self.pages.import_bookmarks("search", SAMPLE)
        self.assertIsNone(self.pages.path("search"))
        self.assertEqual(len(self.pages.state("search")["bookmarks"]), 2)
        self.assertEqual(self.pages.state("global")["bookmarks"], [])
        self.pages.forget("search")
        self.assertEqual(self.pages.state("search")["bookmarks"], [])

    def test_stale_editor_cannot_save_into_a_recreated_profile(self):
        manager = self.window.profile_manager
        self.assertTrue(manager.create_profile("work"))
        dialog = BookmarkDialog(
            self.pages, "work", url="https://example.org/", title="Unsent"
        )
        try:
            self.assertTrue(manager.delete_profile("work"))
            self.assertTrue(manager.create_profile("work"))
            with patch("ui.bookmarks_dialog.QMessageBox.warning") as warning:
                dialog.save()
            self.assertTrue(warning.called)
            self.assertEqual(self.pages.state("work")["bookmarks"], [])
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_builtin_manage_link_opens_native_manager_but_custom_html_does_not(self):
        tab = self.window._new_tab(return_tab=True)
        self.loaded(tab)
        with patch.object(self.window, "_manage_bookmarks") as manage:
            self.js(
                "document.querySelector('a[href=\"steed://action/bookmarks\"]').click()",
                tab,
            )
            self.wait(lambda: manage.called)
        state = self.pages.state("global")
        self.pages.configure(
            "global",
            state["settings"],
            '<title>Custom</title><a href="steed://action/bookmarks">Manage</a>',
        )
        self.loaded(tab)
        with patch.object(self.window, "_manage_bookmarks") as manage:
            self.js("document.querySelector('a').click()", tab)
            for _ in range(10):
                self.app.processEvents()
            manage.assert_not_called()

    def test_newtab_lists_bounded_bookmarks_and_manage_link(self):
        state = self.pages.state("global")
        state["bookmarks"] = [
            {
                "id": f"{i:032x}",
                "url": f"https://example.org/{i}",
                "title": f"Saved {i}",
                "folder": "Work",
            }
            for i in range(30)
        ]
        self.pages._commit("global", state)
        content = self.pages.render("global", "newtab").decode()
        self.assertIn("Saved 23", content)
        self.assertNotIn("Saved 24", content)
        self.assertIn('href="steed://action/bookmarks"', content)
