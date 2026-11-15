"""Extensions' network rules (declarativeNetRequest dynamic and session rules),
kept and applied by Steed: Qt WebEngine's own never finish or end the browser.
Also the background's i18n.getMessage, and rebuilding old installs."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

import extension_build
from browser_window import BrowserWindow
from extension_rules import Rule, url_filter


def request(url, kind="image", initiator="site.test", method="get", page=""):
    return {
        "url": url,
        "host": QUrl(url).host(),
        "initiator": initiator,
        "type": kind,
        "method": method,
        "page": page,
    }


class MatchingTests(unittest.TestCase):
    def rules(self, *raws):
        from extension_rules import ExtensionRules

        store = ExtensionRules(None)
        store.loaded.add("p")
        store.dynamic["p"] = {"ext": [Rule(r, "ext") for r in raws]}
        store.active["p"] = {"ext"}
        store._reindex("p")
        return store

    def test_url_filter_syntax(self):
        domain = url_filter("||ads.example^")
        self.assertTrue(domain.search("https://ads.example/x.js"))
        self.assertTrue(domain.search("https://cdn.ads.example:8080/x"))
        self.assertFalse(domain.search("https://badads.example/x"))
        self.assertFalse(domain.search("https://ads.example.org/x"))
        self.assertTrue(url_filter("|https://a.test/").search("https://a.test/b"))
        self.assertFalse(url_filter("|https://a.test/").search("x https://a.test/"))
        self.assertTrue(url_filter("/ad*.png|").search("https://s.test/ad-12.png"))
        self.assertFalse(url_filter("/ad*.png|").search("https://s.test/ad.png?x"))
        self.assertTrue(url_filter("AD.PNG").search("https://s.test/ad.png"))
        self.assertFalse(url_filter("AD.PNG", True).search("https://s.test/ad.png"))

    def test_conditions(self):
        store = self.rules(
            {
                "id": 1,
                "action": {"type": "block"},
                "condition": {
                    "urlFilter": "track",
                    "initiatorDomains": ["news.test"],
                    "excludedRequestDomains": ["ok.track.test"],
                    "resourceTypes": ["script", "xmlhttprequest"],
                    "requestMethods": ["get"],
                },
            }
        )
        hit = request("https://track.test/t.js", "script", "www.news.test")
        self.assertEqual(store.match("p", hit), ("block",))
        for miss in (
            {**hit, "initiator": "other.test"},
            {**hit, "type": "image"},
            {**hit, "method": "post"},
            {**hit, "url": "https://ok.track.test/t.js", "host": "ok.track.test"},
        ):
            self.assertIsNone(store.match("p", miss), miss)
        # By default a rule leaves the page itself (main_frame) alone.
        every = self.rules({"id": 1, "action": {"type": "block"}, "condition": {}})
        self.assertIsNone(every.match("p", request("https://a.test/", "main_frame")))
        self.assertEqual(every.match("p", request("https://a.test/x")), ("block",))
        third = self.rules(
            {
                "id": 1,
                "action": {"type": "block"},
                "condition": {"domainType": "thirdParty"},
            }
        )
        self.assertIsNone(third.match("p", request("https://cdn.site.test/x")))
        self.assertEqual(third.match("p", request("https://other.test/x")), ("block",))

    def test_priorities_allow_and_page_wide_allow(self):
        store = self.rules(
            {"id": 1, "action": {"type": "block"}, "condition": {"urlFilter": "ad"}},
            {
                "id": 2,
                "priority": 2,
                "action": {"type": "allow"},
                "condition": {"urlFilter": "ad", "initiatorDomains": ["friend.test"]},
            },
            {
                "id": 3,
                "priority": 3,
                "action": {"type": "allowAllRequests"},
                "condition": {
                    "requestDomains": ["trusted.test"],
                    "resourceTypes": ["main_frame"],
                },
            },
        )
        self.assertEqual(store.match("p", request("https://x.test/ad")), ("block",))
        friend = request("https://x.test/ad", initiator="friend.test")
        self.assertEqual(store.match("p", friend), ("allow",))
        on_trusted = request("https://x.test/ad", page="https://trusted.test/home")
        self.assertEqual(store.match("p", on_trusted), ("allow",))
        # Switched off: its rules do nothing.
        store.set_active("p", [])
        self.assertIsNone(store.match("p", request("https://x.test/ad")))

    def test_redirects_and_headers(self):
        store = self.rules(
            {
                "id": 1,
                "action": {
                    "type": "redirect",
                    "redirect": {
                        "transform": {
                            "queryTransform": {"removeParams": ["utm_source"]}
                        }
                    },
                },
                "condition": {
                    "urlFilter": "utm_source=",
                    "resourceTypes": ["main_frame"],
                },
            },
            {
                "id": 2,
                "action": {
                    "type": "redirect",
                    "redirect": {"regexSubstitution": "https://new.test/\\1"},
                },
                "condition": {"regexFilter": "^https://old\\.test/(.*)$"},
            },
            {
                "id": 3,
                "action": {"type": "upgradeScheme"},
                "condition": {"requestDomains": ["plain.test"]},
            },
            {
                "id": 4,
                "action": {
                    "type": "modifyHeaders",
                    "requestHeaders": [
                        {"header": "DNT", "operation": "set", "value": "1"},
                        {"header": "Cookie", "operation": "remove"},
                    ],
                },
                "condition": {"requestDomains": ["api.test"]},
            },
            {
                "id": 5,
                "action": {
                    "type": "redirect",
                    "redirect": {"extensionPath": "/blank.js"},
                },
                "condition": {"urlFilter": "||tracker.test/lib.js"},
            },
        )
        page = request(
            "https://shop.test/p?id=3&utm_source=mail", "main_frame", initiator=""
        )
        self.assertEqual(
            store.match("p", page), ("redirect", "https://shop.test/p?id=3")
        )
        self.assertEqual(
            store.match("p", request("https://old.test/a/b.js")),
            ("redirect", "https://new.test/a/b.js"),
        )
        self.assertEqual(
            store.match("p", request("http://plain.test/x")),
            ("redirect", "https://plain.test/x"),
        )
        self.assertEqual(
            store.match("p", request("https://api.test/v1")), ("headers", {"DNT": "1"})
        )
        self.assertEqual(
            store.match("p", request("https://tracker.test/lib.js", "script")),
            ("redirect", "chrome-extension://ext/blank.js"),
        )

    def test_large_packaged_rulesets_stay_fast(self):
        """Content blockers ship tens of thousands of rules."""
        from extension_rules import ExtensionRules

        raws = [
            {
                "id": i,
                "action": {"type": "block"},
                "condition": {"requestDomains": [f"tracker{i}.test"]},
            }
            for i in range(1, 50001)
        ] + [
            {
                "id": 60000 + i,
                "action": {"type": "block"},
                "condition": {"urlFilter": f"/banner{i}/*.gif"},
            }
            for i in range(2000)
        ]
        store = ExtensionRules(None)
        store.loaded.add("p")
        store.static["p"] = {
            "ext": {"main": [Rule(r, "ext", checked=False) for r in raws]}
        }
        store.active["p"] = {"ext"}
        store._reindex("p")
        started = time.monotonic()
        for i in range(500):
            store.match("p", request(f"https://news{i}.test/story/{i}.js", "script"))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(
            store.match("p", request("https://cdn.tracker123.test/x")), ("block",)
        )
        self.assertEqual(
            store.match("p", request("https://s.test/banner7/a.gif")), ("block",)
        )

    def test_rules_are_filed_by_whole_words_without_missing_any(self):
        from extension_rules import Index, _bounded_word

        self.assertEqual(_bounded_word("/ads/banner", False, False), "ads")
        self.assertIsNone(
            _bounded_word("adserver", False, False)
        )  # Could be inside a word.
        self.assertIsNone(_bounded_word("ad*server", False, False))
        self.assertEqual(_bounded_word("tracker.js", True, False), "tracker")
        self.assertEqual(_bounded_word("/Pixel.gif", False, True), "pixel")  # Longest.
        rules = [
            Rule(
                {
                    "id": 1,
                    "action": {"type": "block"},
                    "condition": {"urlFilter": "/ads/"},
                },
                "e",
            ),
            Rule(
                {
                    "id": 2,
                    "action": {"type": "block"},
                    "condition": {"urlFilter": "adserver"},
                },
                "e",
            ),
            Rule(
                {
                    "id": 3,
                    "action": {"type": "block"},
                    "condition": {
                        "initiatorDomains": ["news.test"],
                        "resourceTypes": ["script"],
                    },
                },
                "e",
            ),
        ]
        index = Index(rules)
        self.assertEqual(list(index.by_word), ["ads"])
        self.assertEqual(index.generic, [rules[1]])
        self.assertEqual(list(index.by_initiator), ["news.test"])

        def found(url, initiator=""):
            return {
                r.id
                for r in index.candidates(
                    QUrl(url).host(), url=url, initiators=(initiator,)
                )
            }

        self.assertIn(1, found("https://x.test/ads/top.js"))
        self.assertNotIn(1, found("https://x.test/downloads/top.js"))
        self.assertIn(2, found("https://myadserver.test/x"))  # Tried everywhere.
        self.assertIn(3, found("https://cdn.test/a.js", "www.news.test"))
        self.assertNotIn(3, found("https://cdn.test/a.js", "other.test"))

    def test_bad_rules_are_refused_whole(self):
        from extension_rules import ExtensionRules

        class Folder:
            def __init__(self, path):
                self.path = path

            def folder(self, name):
                return self.path

        with tempfile.TemporaryDirectory() as directory:
            store = ExtensionRules(Folder(Path(directory)))
            good = {"id": 1, "action": {"type": "block"}, "condition": {}}
            store.update("p", "ext", "dynamic", {"addRules": [good]})
            for bad in (
                {"id": 0, "action": {"type": "block"}},
                {"id": 2, "action": {"type": "launch"}},
                {
                    "id": 2,
                    "action": {"type": "block"},
                    "condition": {"regexFilter": "("},
                },
                {"id": 2, "action": {"type": "redirect"}, "condition": {}},
                {"id": 1, "action": {"type": "block"}, "condition": {}},
            ):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    store.update(
                        "p",
                        "ext",
                        "dynamic",
                        {"addRules": [{**good, "id": 9}, bad]},
                    )
            self.assertEqual(store.get("p", "ext", "dynamic"), [good])
            # Kept on disk for the next run; forgotten when uninstalled.
            again = ExtensionRules(Folder(Path(directory)))
            self.assertEqual(again.get("p", "ext", "dynamic"), [good])
            again.forget("p", "ext")
            self.assertEqual(
                ExtensionRules(Folder(Path(directory))).get("p", "ext", "dynamic"), []
            )

    def test_locale_messages_follow_chromes_order(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for locale, messages in (
                ("en", {"Hi": {"message": "Hello"}, "Bye": {"message": "Bye"}}),
                ("fr", {"hi": {"message": "Salut"}}),
                ("fr_CA", {"bye": {"message": "Bye-bye"}}),
            ):
                (folder / "_locales" / locale).mkdir(parents=True)
                (folder / "_locales" / locale / "messages.json").write_text(
                    json.dumps(messages)
                )
            found = extension_build.locale_messages(
                folder, {"default_locale": "en"}, "fr_CA"
            )
            self.assertEqual(found["hi"]["message"], "Salut")
            self.assertEqual(found["bye"]["message"], "Bye-bye")
            self.assertEqual(extension_build.locale_messages(folder, {}), {})


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/page":
            body = (
                b"<title>Page</title><script>window.got = {};"
                b"for (const name of ['ad', 'old', 'kept']) {"
                b" fetch('/' + name + '.txt').then(r => r.text())"
                b"  .then(t => { got[name] = t; }, () => { got[name] = 'blocked'; }); }"
                b"</script>"
            )
            kind = "text/html"
        else:
            body = path.strip("/").encode()
            kind = "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.name = self.window.profile_manager.current_profile_name
        self.extensions = self.window.profile_manager.registry.extensions
        self.tab = self.window.tabs[self.window.current_tab_index]

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def run_js(self, script):
        result = []
        self.tab.page().runJavaScript(script, result.append)
        self.wait(lambda: result)
        return result[0]

    def install(self):
        folder = Path(self.directory.name) / "rules"
        folder.mkdir()
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 3,
                    "name": "Rules",
                    "version": "1.0",
                    "default_locale": "en",
                    "background": {"service_worker": "bg.js"},
                    "permissions": ["declarativeNetRequest", "storage"],
                    "host_permissions": ["<all_urls>"],
                }
            )
        )
        (folder / "_locales" / "en").mkdir(parents=True)
        (folder / "_locales" / "en" / "messages.json").write_text(
            json.dumps(
                {
                    "greeting": {
                        "message": "Hi $who$, $$5",
                        "placeholders": {"who": {"content": "$1"}},
                    }
                }
            )
        )
        (folder / "bg.js").write_text(
            """
const dnr = chrome.declarativeNetRequest;
(async () => {
  await dnr.updateDynamicRules({addRules: [{id: 1, action: {type: "block"},
    condition: {urlFilter: "/ad.txt"}}], removeRuleIds: [1]});
  await dnr.updateSessionRules({addRules: [{id: 7, action: {type: "redirect",
    redirect: {regexSubstitution: "\\\\1/new.txt"}},
    condition: {regexFilter: "^(.*)/old\\\\.txt$"}}]});
  chrome.storage.local.set({ready: (await dnr.getDynamicRules()).length + "," +
    (await dnr.getSessionRules()).length + "," + chrome.i18n.getMessage("greeting", "Ann")});
})();
"""
        )
        (folder / "probe.html").write_text(
            "<title>probe</title><script src=probe.js></script>"
        )
        (folder / "probe.js").write_text(
            "setInterval(() => chrome.storage.local.get('ready', v => {"
            " if (v.ready) document.title = 'ready ' + v.ready; }), 100);"
        )
        self.extensions.install(self.name, folder)
        self.wait(lambda: self.extensions.installed(self.name))
        (entry,) = self.extensions.installed(self.name)
        return entry

    def test_an_extensions_rules_block_and_redirect_requests(self):
        entry = self.install()
        self.tab.setUrl(QUrl(f"chrome-extension://{entry['id']}/probe.html"))
        self.wait(lambda: self.tab.title().startswith("ready"))
        self.assertEqual(self.tab.title(), "ready 1,1,Hi Ann, $5")
        self.tab.setUrl(QUrl(self.base + "/page"))
        self.wait(lambda: len(self.run_js("window.got || {}") or {}) == 3)
        self.assertEqual(
            self.run_js("window.got"),
            {"ad": "blocked", "old": "new.txt", "kept": "kept.txt"},
        )
        # Dynamic rules are kept on disk; uninstalling forgets them.
        saved = self.extensions.folder(self.name) / "steed_rules.json"
        self.assertEqual(list(json.loads(saved.read_text())), [entry["id"]])
        self.extensions.remove(self.name, entry["id"])
        self.assertEqual(json.loads(saved.read_text()), {})

    def test_packaged_rulesets_apply_and_can_be_switched(self):
        folder = Path(self.directory.name) / "static"
        folder.mkdir()
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 3,
                    "name": "Static",
                    "version": "1.0",
                    "permissions": ["declarativeNetRequest"],
                    "host_permissions": ["<all_urls>"],
                    "declarative_net_request": {
                        "rule_resources": [
                            {"id": "ads", "enabled": True, "path": "ads.json"},
                            {"id": "moved", "enabled": False, "path": "moved.json"},
                        ]
                    },
                }
            )
        )
        (folder / "ads.json").write_text(
            json.dumps(
                [
                    {
                        "id": 1,
                        "action": {"type": "block"},
                        "condition": {"urlFilter": "/ad.txt"},
                    }
                ]
            )
        )
        (folder / "moved.json").write_text(
            json.dumps(
                [
                    {
                        "id": 1,
                        "action": {
                            "type": "redirect",
                            "redirect": {"url": self.base + "/new.txt"},
                        },
                        "condition": {"urlFilter": "/old.txt"},
                    }
                ]
            )
        )
        (folder / "page.html").write_text("<title>ext</title>")
        self.extensions.install(self.name, folder)
        self.wait(lambda: self.extensions.installed(self.name))
        (entry,) = self.extensions.installed(self.name)
        rules = self.extensions.bridges.rules
        self.wait(lambda: rules.index.get(self.name) and rules.index[self.name].count)

        def page():
            self.tab.setUrl(QUrl(self.base + "/page?" + str(time.monotonic())))
            self.wait(lambda: len(self.run_js("window.got || {}") or {}) == 3)
            return self.run_js("window.got")

        self.assertEqual(
            page(), {"ad": "blocked", "old": "old.txt", "kept": "kept.txt"}
        )
        # The extension turns its other ruleset on and one rule off.
        self.tab.setUrl(QUrl(f"chrome-extension://{entry['id']}/page.html"))
        self.wait(lambda: self.tab.title() == "ext")
        self.run_js(
            "const d = chrome.declarativeNetRequest;"
            "d.updateEnabledRulesets({enableRulesetIds: ['moved']})"
            ".then(() => d.updateStaticRules({rulesetId: 'ads', disableRuleIds: [1]}))"
            ".then(() => d.getEnabledRulesets()).then(on => d.getDisabledRuleIds("
            "{rulesetId: 'ads'}).then(off => { document.title = on + '|' + off; }));"
            "1"
        )
        self.wait(lambda: "|" in self.tab.title())
        self.assertEqual(self.tab.title(), "ads,moved|1")
        self.assertEqual(page(), {"ad": "ad.txt", "old": "new.txt", "kept": "kept.txt"})

    def test_store_downloads_do_not_pile_up_in_the_temporary_folder(self):
        import tempfile as temporary

        for content in (b"not an extension", None):
            with temporary.NamedTemporaryFile(suffix=".crx", delete=False) as handle:
                handle.write(content or b"x")
            path = handle.name
            self.extensions._temporary.add(path)
            if content:
                with self.assertRaises((ValueError, OSError)):
                    self.extensions.install(self.name, path)
            else:
                self.extensions.discard_download(path)  # Declined.
            self.assertFalse(Path(path).exists())
        # Files Steed did not download are never deleted.
        mine = Path(self.directory.name) / "mine.crx"
        mine.write_bytes(b"x")
        self.extensions.discard_download(mine)
        self.assertTrue(mine.exists())

    def test_installs_from_an_older_steed_are_built_again(self):
        entry = self.install()
        installed = Path(entry["path"])
        (installed / extension_build.SHIM_NAME).write_text("// an older shim")
        self.extensions._refresh_builds(self.name)

        def fresh():
            found = self.extensions.installed(self.name)
            if not found:
                return False
            path = Path(found[0]["path"]) / extension_build.SHIM_NAME
            return (
                path.is_file()
                and path.read_bytes() == extension_build.SHIM.read_bytes()
            )

        self.wait(fresh)
        self.assertEqual(self.extensions.installed(self.name)[0]["id"], entry["id"])


if __name__ == "__main__":
    unittest.main()
