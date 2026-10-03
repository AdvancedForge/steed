"""Synthetic recipe accounts and API responses; no desktop secrets or user data."""

import copy
import json
import threading

from services.app_oauth import ApiError
from services.connector_recipes import (
    RECIPE_EXAMPLE,
    RECIPE_WRITE_EXAMPLE,
    parse_recipe,
)

TOKEN = "SYNTHETIC_RECIPE_TOKEN"


def recipe_data():
    return copy.deepcopy(RECIPE_EXAMPLE)


def recipe(**changes):
    data = recipe_data()
    data.update(changes)
    return parse_recipe(json.dumps(data).encode())


def write_recipe():
    return parse_recipe(json.dumps(RECIPE_WRITE_EXAMPLE).encode())


class WriteRecipeHttp:
    """Synthetic single-dispatch commits and exact-operation read probes."""

    def __init__(self):
        self.calls, self.operations, self.payloads = [], {}, []
        self.subject, self.label = "synthetic-user", "fixture@example.test"
        self.fail_after_commit = False
        self.fail_once = False
        self.gate = None
        self.entered = threading.Event()

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise ApiError()
        self.calls.append((method, url, headers, body))
        assert headers["Authorization"] == "Bearer " + TOKEN
        if method == "POST":
            assert headers["Content-Type"] == "application/json"
            payload = json.loads(body)
            self.payloads.append(payload)
            operation = (
                payload["operation_id"]
                if "operation_id" in payload
                else payload["data"]["operation_id"]
            )
            result = {
                "operation_id": operation,
                "status": "succeeded",
                "id": "created-" + operation,
            }
            self.operations[operation] = result
            if self.gate:
                self.entered.set()
                self.gate.wait(5)
            if self.fail_after_commit or self.fail_once:
                self.fail_once = False
                raise ApiError()
            return result.copy()
        assert method == "GET" and body is None
        if url.endswith("/me"):
            return {"id": self.subject, "email": self.label}
        if "/operations/" in url:
            return self.operations.get(url.rsplit("/", 1)[1], {}).copy()
        return {"title": "Fixture note", "body": "Shared task data"}


class SyntheticRecipeHttp:
    def __init__(self):
        self.calls = []
        self.subject = "synthetic-user"
        self.label = "fixture@example.test"
        self.body = {
            "title": "Fixture note",
            "body": "Shared task data",
            "other": "UNSHARED_FIELD",
        }
        self.fail = False
        self.gate = None
        self.entered = threading.Event()

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise ApiError()
        self.calls.append((method, url, headers, body))
        if self.gate:
            self.entered.set()
            self.gate.wait(5)
        if self.fail:
            raise ApiError(401)
        assert method == "GET" and body is None
        assert headers == {"Authorization": "Bearer " + TOKEN}
        return (
            {"id": self.subject, "email": self.label}
            if url.endswith("/me")
            else copy.deepcopy(self.body)
        )
