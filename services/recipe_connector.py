"""Trusted recipe interpreter; credentials never enter agent-generated code."""

import json
import re

from services.app_accounts import AccountError
from services.connector_recipes import select
from services.connectors import ConnectorOutcome, ConnectorTool
from services.recipe_http import RecipeHttp, check_cancelled
from services.recipe_oauth import RecipeOAuthAccess, token_http


def bearer_token(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9._~+/-]{8,8192}={0,2}", value
    ):
        raise AccountError("Provide this service's bearer token without spaces")
    return value


def redact(value, credentials):
    """Redact before JSON escaping, including reflected object keys."""
    if isinstance(value, str):
        for credential in credentials:
            value = value.replace(credential, "[credential redacted]")
        return value
    if isinstance(value, list):
        return [redact(v, credentials) for v in value]
    if isinstance(value, dict):
        return {
            redact(k, credentials): redact(v, credentials) for k, v in value.items()
        }
    return value


def identity(recipe, http, token, cancelled):
    check_cancelled(cancelled)
    result = http.request(
        "GET",
        recipe.origin + recipe.identity_path,
        headers={"Authorization": "Bearer " + token},
        cancelled=cancelled,
    )
    subject, label = (
        select(result, recipe.subject_pointer),
        select(result, recipe.label_pointer),
    )
    if type(subject) is int:
        subject = str(subject)
    if (
        not isinstance(subject, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", subject)
        or not isinstance(label, str)
        or not 1 <= len(label) <= 254
        or not label.isprintable()
        or token in subject
        or token in label
    ):
        raise AccountError("The service did not return a usable account identity")
    return subject, label


def connect_recipe(
    recipe, catalog, store, token, capabilities, cancelled, *, http=None
):
    catalog.active(recipe)
    if recipe.authentication.kind != "bearer":
        raise AccountError("Connect this recipe with OAuth sign-in")
    if not capabilities or not set(capabilities) <= {t.name for t in recipe.tools}:
        raise AccountError("Select supported recipe tools")
    token = bearer_token(token)
    store.secrets.check()
    http = http or RecipeHttp(recipe.origin)
    subject, label = identity(recipe, http, token, cancelled)
    catalog.active(recipe)
    check_cancelled(cancelled)
    return store.install(
        recipe.provider,
        subject,
        label,
        capabilities,
        {
            "provider": recipe.provider,
            "recipe_sha256": recipe.sha256,
            "subject": subject,
            "token": token,
        },
        cancelled,
    )


class RecipeConnector:
    def __init__(self, recipe, catalog, account, store, *, http=None):
        self.recipe, self.catalog, self.account, self.store = (
            recipe,
            catalog,
            account,
            store,
        )
        if account.provider != recipe.provider or not set(account.capabilities) <= {
            t.name for t in recipe.tools
        }:
            raise ValueError("Account does not match this recipe")
        self.http = token_http(recipe, http)
        self.oauth = (
            RecipeOAuthAccess(recipe, catalog, account, store, self.http)
            if recipe.authentication.kind == "oauth_pkce"
            else None
        )

    def tools(self):
        return [
            ConnectorTool(
                self.account.connector,
                tool.name,
                "read",
                f"{self.recipe.name} [{self.recipe.origin}] ({self.account.label}): "
                + tool.description
                + (
                    " (exact resource ID)"
                    if "{destination}" in tool.path
                    else " (destination: account)"
                ),
                False,
                lambda request, cancelled, selected=tool: self.execute(
                    selected, request, cancelled
                ),
                validate_destination=tool.destination,
            )
            for tool in self.recipe.tools
            if tool.name in self.account.capabilities
        ]

    def execute(self, tool, request, cancelled):
        self.catalog.active(self.recipe)
        self.store.active(self.account)
        if (
            request.connector != self.account.connector
            or request.tool != tool.name
            or request.effect != "read"
            or request.artifact is not None
        ):
            raise ValueError("Recipe request does not match the configured tool")
        destination = tool.destination(request.destination)
        credential = (
            self.oauth.credential(cancelled)
            if self.oauth
            else self.store.secrets.load(self.account.account_id)
        )
        if (
            credential.get("provider") != self.recipe.provider
            or credential.get("recipe_sha256") != self.recipe.sha256
            or credential.get("subject") != self.account.subject
        ):
            raise AccountError("Credential does not match the installed recipe/account")
        token = bearer_token(
            credential.get("access_token") if self.oauth else credential.get("token")
        )
        if identity(self.recipe, self.http, token, cancelled) != (
            self.account.subject,
            self.account.label,
        ):
            raise AccountError("Account identity changed; reconnect this service")
        self.catalog.active(self.recipe)
        self.store.active(self.account)
        check_cancelled(cancelled)
        result = self.http.request(
            "GET",
            self.recipe.origin + tool.path.replace("{destination}", destination),
            headers={"Authorization": "Bearer " + token},
            cancelled=cancelled,
        )
        check_cancelled(cancelled)
        self.catalog.active(self.recipe)
        self.store.active(self.account)
        credentials = tuple(
            value
            for value in (
                token,
                credential.get("refresh_token"),
                credential.get("client_secret"),
            )
            if isinstance(value, str) and value
        )
        text = json.dumps(
            redact(
                {label: select(result, path) for label, path in tool.response},
                credentials,
            ),
            ensure_ascii=False,
            allow_nan=False,
        )
        return ConnectorOutcome(text=text).validate()
