"""Whole-batch validation and task identity cannot be relaxed by chat text."""

import json
import unittest

from services.chatbot_protocol import RELAY_TOOLS, RelayError, catalogue, parse_response
from services.task_client import CLIENT_COMMANDS


def response(*calls, exchange="current", **extra):
    return json.dumps(
        {"version": 1, "exchange_id": exchange, "calls": list(calls), **extra}
    )


def call(tool="steed_task_status", arguments=None, identity="one"):
    return {"id": identity, "tool": tool, "arguments": arguments or {}}


class ChatbotProtocolTests(unittest.TestCase):
    def test_catalogue_exposes_only_task_client_tools(self):
        self.assertTrue(all(t.command in CLIENT_COMMANDS for t in RELAY_TOOLS.values()))
        self.assertTrue(
            all(
                "task_id" not in t.input_schema["properties"]
                for t in RELAY_TOOLS.values()
            )
        )
        self.assertEqual({t["name"] for t in catalogue()}, set(RELAY_TOOLS))

    def test_exact_fence_and_immutable_arguments(self):
        parsed = parse_response("```json\n" + response(call()) + "\n```", "current")
        parsed.calls[0].arguments["task_id"] = "other"
        self.assertEqual(parsed.calls[0].arguments, {})
        self.assertEqual(parse_response(parsed.canonical, "current"), parsed)

    def test_final_answer(self):
        parsed = parse_response(
            json.dumps(
                {
                    "version": 1,
                    "exchange_id": "current",
                    "final": "Done\nCheck results.",
                }
            ),
            "current",
        )
        self.assertEqual(parsed.final, "Done\nCheck results.")
        self.assertEqual(parsed.calls, ())

    def test_envelope_and_parser_fail_closed(self):
        valid = response(call())
        malformed = [
            valid.replace('"version": 1', '"version": true'),
            valid.replace('"version": 1', '"version": 1.0'),
            valid.replace('"version": 1', '"version": 1, "version": 1'),
            valid + " trailing text",
            "Here is JSON: " + valid,
            "[]",
            "null",
            "{",
            "[" * 1000 + "]" * 1000,
            response(call(), task_id="other"),
            response(call(), final="done"),
            response(),
            response(*[call(identity=str(n)) for n in range(5)]),
            response(call(arguments={"task_id": "other"})),
            response(call(tool="steed_grant_approve")),
            response(call(tool="steed_task_start")),
            response(call(tool="steed_page_capture")),
            response(call(tool="eval")),
            response(call(identity="one"), call(identity="one")),
            " " * 65537,
            "\ud800",
        ]
        for text in malformed:
            with self.subTest(text=text[:80]), self.assertRaises(RelayError):
                parse_response(text, "current")

    def test_nonce_and_consumed_ids(self):
        for expected, used in [("different", ()), ("", ()), ("current", ("one",))]:
            with (
                self.subTest(expected=expected, used=used),
                self.assertRaises(RelayError),
            ):
                parse_response(response(call()), expected, used)

    def test_nested_scope_types_and_bounds(self):
        invalid = [
            {"scope": {"actions": ["grant.approve"]}},
            {"scope": {"allow_private_network": 1}},
            {"scope": {"origins": [4]}},
            {"scope": {"duration_seconds": True}},
            {"scope": {"duration_seconds": 86401}},
            {"scope": {"unknown": "value"}},
            {"scope": {"connector_permissions": [{"connector": "a"}]}},
            {"scope": {"actions": ["read"] * 18}},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(RelayError):
                parse_response(
                    response(call("steed_grant_request", arguments)), "current"
                )
        parsed = parse_response(
            response(
                call(
                    "steed_grant_request",
                    {"scope": {"actions": ["read"], "allow_private_network": True}},
                )
            ),
            "current",
        )
        self.assertTrue(parsed.calls)

    def test_duplicate_nested_keys_nonfinite_and_complex_values(self):
        for text in [
            response(
                call(
                    "steed_grant_request", {"scope": {"duration_seconds": float("nan")}}
                )
            ),
            response(
                call(
                    "steed_grant_request", {"scope": {"duration_seconds": float("inf")}}
                )
            ),
            response(call()).replace(
                '"arguments": {}', '"arguments": {"a": 1, "a": 2}'
            ),
            response(call(arguments={"x": "\x00"})),
            response(call("steed_session_open", {"profile": "\ud800"})),
            response(call(arguments={"\ud800": "invalid key"})),
            response(call(arguments={"x": [0] * 4100})),
        ]:
            with self.subTest(text=text[:80]), self.assertRaises(RelayError):
                parse_response(text, "current")

    def test_final_bounds_and_control_characters(self):
        for final in ["", " ", 4, "x" * 8193, "\x01", "\ud800"]:
            with self.subTest(final=str(final)[:20]), self.assertRaises(RelayError):
                parse_response(
                    json.dumps(
                        {"version": 1, "exchange_id": "current", "final": final}
                    ),
                    "current",
                )
