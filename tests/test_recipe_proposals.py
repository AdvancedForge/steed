"""Agent proposal ownership, trusted reviews, account switches and revocation."""

import base64
import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app_connector_support import MemorySecrets
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application
from recipe_support import TOKEN, SyntheticRecipeHttp, recipe
from test_recipe_oauth import OAuthHttp, oauth_recipe

from services.app_accounts import AccountStore, ClientSetupRequired
from services.app_connections import AppConnections
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.app_connections import AppConnectionsDialog
from ui.recipe_review import RecipeReviewDialog
from ui.task_dialog import TaskDialog


class RecipeProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.secrets = MemorySecrets()
        self.accounts = AccountStore(self.root / "accounts.json", self.secrets)
        self.http = SyntheticRecipeHttp()
        self.manager = AppConnections(
            self.accounts.path,
            store=self.accounts,
            providers=(),
            recipe_http_factory=lambda recipe: self.http,
        )
        self.service = TaskService(
            self.root / "tasks.sqlite",
            connectors=self.manager.connectors,
            connections=self.manager,
        )
        self.manager.task_service = self.service
        self.client = self.task()
        self.dialogs = []

    def tearDown(self):
        if self.http.gate:
            self.http.gate.set()
        self.wait_for(
            lambda: not self.manager.busy and not self.service._connector_jobs
        )
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.close()
                sip.delete(dialog)
        self.manager.close()
        self.service.close()
        sip.delete(self.manager)
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait_for(self, condition):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.002)
        self.fail("Recipe fixture did not settle")

    def call(self, command, client=None, **args):
        future = (client or self.client).submit(command, **args)
        self.wait_for(future.done)
        return future.result()

    def task(self, actions=None, **scope):
        task = self.service._create("Design a synthetic service connection")
        grant = self.service._request_grant(
            task.task_id, {"actions": actions or ["workspace", "connector"], **scope}
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        return self.service.client(task.task_id)

    def upload(self, raw=None, name="recipe.json"):
        raw = raw or recipe().raw
        item = self.call(
            "workspace.begin",
            name=name,
            media_type="application/json",
            byte_count=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        self.call(
            "workspace.write",
            file_id=item.file_id,
            offset=0,
            data_base64=base64.b64encode(raw).decode(),
        )
        return self.call("workspace.seal", file_id=item.file_id)

    def propose(self):
        item = self.upload()
        return self.call("recipe.propose", file_id=item.file_id, request_id="install-1")

    def install(self):
        proposal = self.propose()
        self.service.resolve_recipe(
            self.client.task_id, proposal.request_id, approve=True
        )
        return recipe()

    def connect(self):
        installed = self.install()
        self.manager.connect_recipe_account(installed.provider, TOKEN, ["read_note"])
        self.wait_for(lambda: not self.manager.busy)
        self.assertFalse(self.manager.failure)
        return self.accounts.list()[0]

    def test_proposal_grants_nothing_until_exact_trusted_popup_and_never_replays(self):
        proposal = self.propose()
        self.assertEqual(proposal.state, "awaiting_approval")
        self.assertFalse(self.manager.providers)
        self.assertEqual(self.http.calls, [])
        dialog = RecipeReviewDialog(
            self.manager,
            self.service.recipes.recipe(proposal),
            service=self.service,
            proposal=proposal,
        )
        self.dialogs.append(dialog)
        self.assertTrue(dialog.deny_button.isDefault())
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-recipe-review.png")
        dialog.allow_button.click()
        self.assertIn(proposal.provider, self.manager.providers)
        self.assertEqual(self.accounts.list(), ())
        repeated = self.call(
            "recipe.propose", file_id=proposal.file_id, request_id=proposal.request_id
        )
        self.assertEqual(repeated.state, "installed")
        self.assertNotIn("recipe.resolve", self.service.broker._handlers)
        self.assertNotIn("app.install_recipe", self.service.broker._handlers)

    def test_scope_and_foreign_source_boundaries(self):
        proposal = self.propose()
        other = self.task()
        with self.assertRaises(KeyError):
            self.call(
                "recipe.propose",
                client=other,
                file_id=proposal.file_id,
                request_id="foreign",
            )
        only_workspace = self.task(["workspace"])
        with self.assertRaises(TaskPermissionError):
            self.call("recipe.status", client=only_workspace, request_id="install-1")
        item = self.upload(name="other.json")
        with self.assertRaises(ValueError):
            self.call(
                "recipe.propose", file_id=item.file_id, request_id=proposal.request_id
            )

    def test_deleted_source_and_ended_task_cancel_pending_installations(self):
        proposal = self.propose()
        self.service.delete_workspace_file(self.client.task_id, proposal.file_id)
        self.assertEqual(
            self.service.recipes.get(self.client.task_id, proposal.request_id).state,
            "cancelled",
        )
        with self.assertRaises(ValueError):
            self.service.resolve_recipe(
                self.client.task_id, proposal.request_id, approve=True
            )
        second = self.upload(name="second.json")
        self.call("recipe.propose", file_id=second.file_id, request_id="second")
        self.service.cancel_task(self.client.task_id)
        self.assertEqual(
            self.service.recipes.get(self.client.task_id, "second").state, "cancelled"
        )

    def test_denial_restart_and_request_limits_do_not_install(self):
        proposal = self.propose()
        self.service.resolve_recipe(
            self.client.task_id, proposal.request_id, approve=False
        )
        self.assertEqual(
            self.call("recipe.status", request_id=proposal.request_id).state, "denied"
        )
        item = self.upload(name="many.json")
        for i in range(3):
            self.call("recipe.propose", file_id=item.file_id, request_id=f"pending-{i}")
        with self.assertRaises(TaskPermissionError):
            self.call("recipe.propose", file_id=item.file_id, request_id="fourth")
        # Simulate exit after task shutdown but before proposal cleanup. The
        # next process must terminalize these persisted requests, never replay.
        with patch.object(self.service.recipes, "cancel_task"):
            self.service.close()
        sip.delete(self.service)
        self.service = TaskService(
            self.root / "tasks.sqlite",
            connectors=self.manager.connectors,
            connections=self.manager,
        )
        self.manager.task_service = self.service
        self.assertTrue(
            all(
                p.state == "interrupted"
                for p in self.service.recipes.list(self.client.task_id)[1:]
            )
        )
        self.assertFalse(self.manager.providers)

    def test_installed_account_uses_existing_access_switch_and_receipt_broker(self):
        account = self.connect()
        self.manager.set_access(account.account_id, "read", True)
        task = self.service._create("Read a synthetic note")
        grant = self.service._request_grant(
            task.task_id,
            {
                "actions": ["app"],
                "connector_permissions": [
                    {
                        "connector": account.connector,
                        "tool": "read_note",
                        "effect": "read",
                        "destination": "note-1",
                    }
                ],
            },
        )
        self.assertEqual(grant.status, "approved")
        client = self.service.client(task.task_id)
        self.call(
            "app.call_tool",
            client=client,
            connector=account.connector,
            tool="read_note",
            request_id="read-1",
            destination="note-1",
        )
        self.wait_for(
            lambda: (
                self.service.connector_store.get(task.task_id, "read-1").state
                not in {"requested", "running"}
            )
        )
        result = self.call("app.status", client=client, request_id="read-1")
        self.assertEqual(result.state, "succeeded")
        self.assertIn("Shared task data", result.text)
        self.assertNotIn(TOKEN, repr(result))
        calls = len(self.http.calls)
        self.call(
            "app.call_tool",
            client=client,
            connector=account.connector,
            tool="read_note",
            request_id="read-1",
            destination="note-1",
        )
        self.assertEqual(calls, len(self.http.calls))
        self.manager.set_access(account.account_id, "read", False)
        self.assertEqual(self.service.store.get(task.task_id).status, "cancelled")

    def test_recipe_removal_revokes_pending_calls_and_does_not_restore_on_restart(self):
        account = self.connect()
        client = self.task(
            ["app"],
            connector_permissions=[
                {
                    "connector": account.connector,
                    "tool": "read_note",
                    "effect": "read",
                    "destination": "note-1",
                }
            ],
        )
        self.http.gate = threading.Event()
        self.call(
            "app.call_tool",
            client=client,
            connector=account.connector,
            tool="read_note",
            request_id="waiting",
            destination="note-1",
        )
        self.wait_for(self.http.entered.is_set)
        self.manager.remove_recipe(account.provider)
        self.http.gate.set()
        self.wait_for(
            lambda: not self.manager.busy and not self.service._connector_jobs
        )
        self.assertFalse(self.accounts.list()[0].enabled)
        self.assertNotIn(account.account_id, self.secrets.values)
        self.assertFalse(self.manager.connectors.describe())
        self.assertEqual(self.service.store.get(client.task_id).status, "cancelled")
        self.assertNotEqual(
            self.service.connector_store.get(client.task_id, "waiting").state,
            "succeeded",
        )

    def test_catalog_failure_cannot_complete_installation(self):
        proposal = self.propose()
        with patch(
            "services.connector_recipes.save_json", side_effect=OSError("disk failure")
        ):
            with self.assertRaises(OSError):
                self.service.resolve_recipe(
                    self.client.task_id, proposal.request_id, approve=True
                )
        self.assertFalse(self.manager.providers)
        self.assertEqual(self.accounts.list(), ())
        self.assertTrue(self.service.failure)

    def test_preflight_is_owned_offline_and_creates_no_installation_or_account(self):
        item = self.upload()
        checked = self.call("recipe.validate", file_id=item.file_id)
        self.assertTrue(checked["valid"])
        self.assertTrue(checked["installation_required"])
        self.assertFalse(checked["provider_semantics_verified"])
        self.assertEqual(checked["recipe"]["provider"], recipe().provider)
        self.assertEqual(self.service.recipes.list(self.client.task_id), [])
        self.assertEqual(self.accounts.list(), ())
        self.assertFalse(self.manager.providers)
        self.assertFalse(self.http.calls)
        bad = self.upload(
            b'{"version":1,"credentials":"SYNTHETIC_SECRET"}', name="bad.json"
        )
        result = self.call("recipe.validate", file_id=bad.file_id)
        self.assertFalse(result["valid"])
        self.assertNotIn("SYNTHETIC_SECRET", str(result))
        foreign = self.task()
        with self.assertRaises(KeyError):
            self.call("recipe.validate", client=foreign, file_id=item.file_id)
        no_scope = self.task(["workspace"])
        with self.assertRaises(TaskPermissionError):
            self.call("recipe.validate", client=no_scope, file_id=item.file_id)

    def test_reinstall_after_failed_removal_never_restores_old_account_authority(self):
        account = self.connect()
        self.manager.set_access(account.account_id, "read", True)
        with patch.object(
            self.accounts, "disable", side_effect=OSError("disk failure")
        ):
            with self.assertRaises(OSError):
                self.manager.remove_recipe(account.provider)
        self.assertTrue(self.accounts.list()[0].enabled)
        with self.assertRaises(ValueError):
            self.manager.recipes.active(recipe())
        self.manager.install_recipe(recipe())
        self.assertFalse(self.accounts.list()[0].enabled)
        self.assertFalse(self.manager.connectors.describe())

    def test_oauth_settings_connection_collection_read_and_removal_share_native_controls(
        self,
    ):
        custom = oauth_recipe()
        self.http = OAuthHttp()
        self.manager.install_recipe(custom)
        path = self.root / "client.json"
        path.write_text('{"client_id":"SYNTHETIC_CLIENT"}')
        panel = AppConnectionsDialog(self.manager)
        self.dialogs.append(panel)
        self.assertFalse(panel.setup_button.isHidden())
        panel.client.setText(str(path))
        self.manager.install_recipe(recipe())
        panel.provider.setCurrentIndex(panel.provider.findData(recipe().provider))
        self.assertEqual(panel.client.text(), "")
        panel.provider.setCurrentIndex(panel.provider.findData(custom.provider))
        panel.show()
        panel.setup_button.click()
        panel.grab().save("/tmp/steed-recipe-oauth-settings.png")
        review = RecipeReviewDialog(self.manager, custom)
        self.dialogs.append(review)
        review.show()
        self.app.processEvents()
        review.grab().save("/tmp/steed-recipe-oauth-review.png")
        with patch(
            "services.recipe_oauth.desktop_authorization",
            return_value=(
                "SYNTHETIC_CODE",
                "SYNTHETIC_VERIFIER",
                "http://127.0.0.1:12345/callback",
            ),
        ) as authorize:
            self.manager.connect_account(
                custom.provider, path, ["read_note", "list_notes"]
            )
            self.wait_for(lambda: not self.manager.busy)
        self.assertFalse(authorize.call_args.kwargs["google_options"])
        account = self.accounts.list()[0]
        self.manager.set_access(account.account_id, "read", True)
        task = self.service._create("Read a synthetic collection")
        permission = {
            "connector": account.connector,
            "tool": "list_notes",
            "effect": "read",
            "destination": "account",
        }
        grant = self.service._request_grant(
            task.task_id, {"actions": ["app"], "connector_permissions": [permission]}
        )
        self.assertEqual(grant.status, "approved")
        client = self.service.client(task.task_id)
        self.call(
            "app.call_tool",
            client=client,
            connector=account.connector,
            tool="list_notes",
            destination="account",
            request_id="collection",
        )
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(
            self.call("app.status", client=client, request_id="collection").state,
            "succeeded",
        )
        self.manager.remove_recipe(custom.provider)
        self.wait_for(lambda: not self.manager.busy)
        with self.assertRaises(ClientSetupRequired):
            self.accounts.client_configuration(custom.provider)
        self.assertNotIn(account.account_id, self.secrets.values)

    def test_trusted_settings_manage_recipes_and_pending_task_reviews(self):
        proposal = self.propose()
        panel = TaskDialog(self.service)
        self.dialogs.append(panel)
        panel.table.selectRow(0)
        panel.refresh_workers()
        panel.recipe_requests.selectRow(0)
        self.assertTrue(panel.review_recipe_button.isEnabled())
        self.service.resolve_recipe(
            self.client.task_id, proposal.request_id, approve=True
        )
        connections = AppConnectionsDialog(self.manager)
        self.dialogs.append(connections)
        self.assertEqual(connections.provider.currentData(), proposal.provider)
        self.assertFalse(connections.setup_button.isVisible())
        self.assertTrue(connections.remove_recipe_button.isEnabled())
        self.assertTrue(connections.import_recipe_button.isEnabled())
        connections.show()
        self.app.processEvents()
        connections.grab().save("/tmp/steed-recipe-connections.png")
