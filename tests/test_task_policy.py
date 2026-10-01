"""Exact scope matching, live budgets, and thread-safe grant termination."""

import threading
import unittest

from services.task_policy import LiveGrant, TaskPermissionError, TaskScope, origin_url


class TaskPolicyTests(unittest.TestCase):
    def scope(self, **changes):
        values = {"origins": ["https://example.org"], "allow_private_network": True}
        values.update(changes)
        return TaskScope.parse(values)

    def test_origin_normalization_and_rejected_ambiguous_authority(self):
        self.assertEqual(origin_url("HTTPS://Example.ORG:443/"), "https://example.org")
        self.assertEqual(origin_url("http://[::1]:8080"), "http://[::1]:8080")
        for value in (
            "https://example.org/path",
            "https://user:secret@example.org",
            "https://*.example.org",
            "https://example.org?query",
            "file:///tmp",
            "https://example.org.",
            "http://127.1",
            "http://2130706433",
            "http://0x7f000001",
            "https://example.org:0",
            "https://example.org/#x",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                origin_url(value)

    def test_exact_origin_checks_all_url_parts_without_suffix_matching(self):
        grant = LiveGrant(self.scope())
        grant.check("navigate", url="https://example.org/path?q=one#fragment")
        grant.check("navigate", url="about:blank")
        for url in (
            "http://example.org",
            "https://example.org:444",
            "https://sub.example.org",
            "https://example.org.attacker.invalid",
            "https://example.org@attacker.invalid",
            "https://user@example.org",
            "data:text/html,fixture",
            "file:///etc/passwd",
            "https://example.org./",
            "blob:https://example.org/document",
        ):
            with self.subTest(url=url), self.assertRaises(TaskPermissionError):
                grant.check("navigate", url=url)

    def test_network_permission_is_explicit_and_scope_stays_exact(self):
        for origin in (
            "http://127.0.0.1",
            "http://[::1]",
            "http://10.0.0.1",
            "http://localhost",
            "https://example.org",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                TaskScope.parse({"origins": [origin]})
        scope = TaskScope.parse({"origins": ["https://8.8.8.8"]})
        self.assertFalse(scope.allow_private_network)
        grant = LiveGrant(self.scope(origins=["http://127.0.0.1:8000"]))
        self.assertEqual(grant.request("http://127.0.0.1:8000/path"), "")
        self.assertEqual(
            grant.request("http://127.0.0.1:8001/path"), "origin_out_of_scope"
        )

    def test_invalid_scope_fields_profiles_actions_and_budgets_are_rejected(self):
        for changes in (
            {"profile": "global"},
            {"actions": ["upload"]},
            {"file_destinations": ["/tmp/a"]},
            {"actions": "navigate"},
            {"origins": "https://example.org"},
            {"allow_private_network": "yes"},
            {"max_operations": True},
            {"duration_seconds": 0},
            {"max_requests": 100001},
            {"max_sessions": 11},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.scope(**changes)

    def test_actions_profile_and_file_destination_fail_before_consumption(self):
        grant = LiveGrant(self.scope(actions=["inspect"]))
        grant.check("inspect")
        for action, arguments in (
            ("navigate", {"url": "https://example.org"}),
            ("open", {"profile": "work"}),
            ("inspect", {"destination": "/tmp/forbidden"}),
            ("download", {"destination": "/tmp/forbidden"}),
        ):
            with self.subTest(action=action), self.assertRaises(TaskPermissionError):
                grant.check(action, **arguments)
        self.assertEqual(grant.operations, 0)

    def test_expiration_and_revocation_block_both_command_and_engine_paths(self):
        now = [20.0]
        grant = LiveGrant(self.scope(duration_seconds=2), clock=lambda: now[0])
        grant.check("inspect")
        now[0] = 22.0
        self.assertEqual(grant.reason(), "expired")
        self.assertEqual(grant.request("https://example.org"), "expired")
        with self.assertRaises(TaskPermissionError):
            grant.check("inspect")
        other = LiveGrant(self.scope())
        other.end("revoked")
        other.end("completed")
        self.assertEqual(other.reason(), "revoked")
        self.assertEqual(other.navigation("https://example.org"), "revoked")

    def test_operation_and_request_limits_and_unsupported_methods(self):
        grant = LiveGrant(self.scope(max_operations=1, max_requests=1))
        self.assertEqual(
            grant.request("https://example.org", b"POST"), "method_out_of_scope"
        )
        self.assertEqual(grant.requests, 0)
        grant.consume_operation()
        with self.assertRaises(TaskPermissionError):
            grant.consume_operation()
        self.assertEqual(grant.reason(), "operation_budget_exhausted")
        other = LiveGrant(self.scope(max_requests=1))
        self.assertEqual(other.request("https://example.org", b"HEAD"), "")
        self.assertEqual(
            other.request("https://example.org"), "request_budget_exhausted"
        )
        self.assertEqual(
            other.navigation("https://example.org"), "request_budget_exhausted"
        )

    def test_concurrent_network_requests_cannot_overspend_budget(self):
        grant = LiveGrant(self.scope(max_requests=7))
        barrier = threading.Barrier(20)
        results = []

        def request():
            barrier.wait()
            results.append(grant.request("https://example.org"))

        workers = [threading.Thread(target=request) for _ in range(20)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(results.count(""), 7)
        self.assertEqual(grant.requests, 7)
