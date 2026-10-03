"""Synthetic recipe accounts and API responses; no desktop secrets or user data."""

import copy
import json
import threading

from services.app_oauth import ApiError
from services.connector_recipes import RECIPE_EXAMPLE, parse_recipe

TOKEN = "SYNTHETIC_RECIPE_TOKEN"


def recipe_data():
    return copy.deepcopy(RECIPE_EXAMPLE)


def recipe(**changes):
    data = recipe_data()
    data.update(changes)
    return parse_recipe(json.dumps(data).encode())


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
