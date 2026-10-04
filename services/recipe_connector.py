"""Trusted recipe interpreter; credentials never enter agent-generated code."""

import json
import re
from urllib.parse import urlencode

from services.app_accounts import AccountError
from services.connector_recipes import (
    RecipeError,
    page_cursor,
    read_inputs,
    select,
)
from services.connectors import ConnectorOutcome, ConnectorTool
from services.recipe_http import check_cancelled
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


def input_schema(tool):
    """Bounded agent-visible description of each tool's typed read inputs."""
    return tuple(
        {
            "name": item.name,
            "type": item.kind,
            **({"max_length": item.maximum_length} if item.kind == "string" else {}),
            **(
                {"minimum": item.minimum, "maximum": item.maximum}
                if item.kind == "integer"
                else {}
            ),
            **({"default": item.default} if item.has_default else {}),
        }
        for item in tool.inputs
    )


def tool_description(recipe, account, tool):
    label = account.label if len(account.label) <= 64 else account.label[:63] + "…"
    prefix = f"{recipe.name} [{recipe.origin}] ({label}): "
    suffix = (
        " (one exact email recipient; payload review)"
        if tool.destination_type == "email"
        else " (exact resource ID)"
        if "{destination}" in tool.path
        else " (destination: account)"
    )
    if tool.pagination:
        suffix += f" (up to {tool.pagination.max_pages} pages)"
    if tool.inputs:
        suffix += f" ({len(tool.inputs)} reviewed inputs)"
    if tool.query is not None:
        suffix += " (one fixed GraphQL read; no query text)"
    if tool.steps:
        suffix += f" ({len(tool.steps)} separately reviewed steps)"
    budget = 512 - len(prefix) - len(suffix)
    description = (
        tool.description
        if len(tool.description) <= budget
        else tool.description[: budget - 1] + "…"
    )
    return prefix + description + suffix


def identity_label(recipe, result, subject, credentials):
    """Use only reviewed labels; absence may fall back, malformed data may not."""
    for path in (recipe.label_pointer, *recipe.label_alternatives):
        try:
            value = result
            for part in path[1:].split("/"):
                if value is None and recipe.label_alternatives:
                    break
                value = select(value, "/" + part)
        except (KeyError, IndexError):
            if recipe.label_alternatives:
                continue
            raise
        if recipe.label_alternatives and (value is None or value == ""):
            continue
        if recipe.label_alternatives and path == recipe.subject_pointer:
            value = subject
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 254
            or not value.isprintable()
            or recipe.label_alternatives
            and not value.strip()
            or any(secret in value for secret in credentials)
        ):
            raise AccountError("The service returned an invalid reviewed account label")
        return value
    raise AccountError("The service returned no usable reviewed account label")


def application_error(response, errors, credentials):
    """Report an HTTP-200 application failure honestly instead of empty fields."""
    if errors is None:
        return
    try:
        value = select(response, errors.pointer)
    except (KeyError, IndexError, RecipeError):
        return  # An absent error field means the read did not report one.
    if value in (None, "", [], {}):
        return
    try:
        code, message = select(value, errors.code), select(value, errors.message)
    except (KeyError, IndexError, RecipeError):
        code, message = "", ""
    detail = (
        {"code": code, "message": message}
        if isinstance(code, str) and isinstance(message, str)
        else {}
    )
    raise RecipeError(
        "The service reported an application error: "
        + json.dumps(redact(detail, credentials), ensure_ascii=False)[:512]
    )


def graphql_result(response, rule, credentials):
    """A fixed reviewed read never returns partial data as if it had succeeded."""
    try:
        errors = select(response, rule.errors_pointer)
    except (KeyError, IndexError, RecipeError):
        errors = None
    if errors not in (None, "", [], {}):
        entries = []
        for item in errors if isinstance(errors, list) else [errors]:
            if not isinstance(item, dict):
                continue
            extensions = item.get("extensions")
            entries.append(
                {
                    key: value
                    for key, value in (
                        ("message", item.get("message")),
                        (
                            "code",
                            extensions.get("code")
                            if isinstance(extensions, dict)
                            else item.get("code"),
                        ),
                    )
                    if isinstance(value, str)
                }
            )
            if len(entries) == 5:
                break
        raise RecipeError(
            "The reviewed GraphQL read returned errors: "
            + json.dumps(redact(entries, credentials), ensure_ascii=False)[:1024]
        )
    try:
        data = select(response, rule.data_pointer)
    except (KeyError, IndexError, RecipeError):
        data = None
    if data is None:
        raise RecipeError("The reviewed GraphQL read returned no data")
    return data


def identity(recipe, http, token, cancelled, *, credentials=()):
    check_cancelled(cancelled)
    headers = {**dict(recipe.request_headers), "Authorization": "Bearer " + token}
    body, method = None, "GET"
    if recipe.identity_request is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
        body = json.dumps(
            recipe.identity_request.materialize({}),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    result = http.request(
        method,
        recipe.origin + recipe.identity_path,
        headers=headers,
        body=body,
        cancelled=cancelled,
    )
    subject = select(result, recipe.subject_pointer)
    secrets = tuple(
        value for value in (token, *credentials) if isinstance(value, str) and value
    )
    if type(subject) is int:
        subject = str(subject)
    if (
        not isinstance(subject, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", subject)
        or any(secret in subject for secret in secrets)
    ):
        raise AccountError("The service did not return a usable account identity")
    label = identity_label(recipe, result, subject, secrets)
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
    from services.recipe_oauth import token_http

    http = http or token_http(recipe)
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
        from services.recipe_plans import RecipePlan
        from services.recipe_writes import RecipeWrites

        writes = RecipeWrites(self)
        return [
            ConnectorTool(
                self.account.connector,
                tool.name,
                tool.effect,
                tool_description(self.recipe, self.account, tool),
                tool.effect != "read",
                lambda request, cancelled, selected=tool: (
                    self.execute if selected.effect == "read" else writes.execute
                )(selected, request, cancelled),
                validate_destination=tool.destination,
                prepare_review=(
                    lambda request, cancelled, selected=tool: writes.prepare(
                        selected, request, cancelled
                    )
                )
                if tool.effect != "read"
                else None,
                reconcile=(
                    lambda probe, cancelled, selected=tool: writes.reconcile(
                        selected, probe, cancelled
                    )
                )
                if tool.reconciliation
                else None,
                review_kind="payload" if tool.effect != "read" else "document",
                plan=RecipePlan(self, tool) if tool.steps else None,
                validate_inputs=(
                    lambda values, selected=tool: read_inputs(values, selected.inputs)
                )
                if tool.inputs
                else None,
                input_schema=input_schema(tool),
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
        inputs = read_inputs(request.inputs, tool.inputs)
        token, credentials = self.authorize(cancelled)
        result = (
            self.typed_read(tool, destination, inputs, token, credentials, cancelled)
            if tool.method == "POST"
            else self.read(tool, destination, token, credentials, cancelled)
        )
        check_cancelled(cancelled)
        self.catalog.active(self.recipe)
        self.store.active(self.account)
        text = json.dumps(
            redact(result, credentials),
            ensure_ascii=False,
            allow_nan=False,
        )
        return ConnectorOutcome(text=text).validate()

    def authorize(self, cancelled):
        self.catalog.active(self.recipe)
        self.store.active(self.account)
        check_cancelled(cancelled)
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
        credentials = tuple(
            value
            for value in (
                token,
                credential.get("refresh_token"),
                credential.get("client_secret"),
            )
            if isinstance(value, str) and value
        )
        if identity(
            self.recipe, self.http, token, cancelled, credentials=credentials
        ) != (
            self.account.subject,
            self.account.label,
        ):
            raise AccountError("Account identity changed; reconnect this service")
        self.catalog.active(self.recipe)
        self.store.active(self.account)
        check_cancelled(cancelled)
        return token, credentials

    def read(self, tool, destination, token, credentials, cancelled):
        base = self.recipe.origin + tool.path.replace("{destination}", destination)
        url, merged, seen = base, {label: [] for label, _ in tool.response}, set()
        pages = tool.pagination.max_pages if tool.pagination else 1
        for index in range(pages):
            self.catalog.active(self.recipe)
            self.store.active(self.account)
            check_cancelled(cancelled)
            if index and identity(
                self.recipe, self.http, token, cancelled, credentials=credentials
            ) != (
                self.account.subject,
                self.account.label,
            ):
                raise AccountError("Account identity changed during pagination")
            response = self.http.request(
                "GET",
                url,
                headers={
                    **dict(self.recipe.request_headers),
                    "Authorization": "Bearer " + token,
                },
                cancelled=cancelled,
            )
            check_cancelled(cancelled)
            self.catalog.active(self.recipe)
            self.store.active(self.account)
            selected = {label: select(response, path) for label, path in tool.response}
            if not tool.pagination:
                return selected
            for label, values in selected.items():
                if not isinstance(values, list):
                    raise RecipeError("Paginated response fields must be arrays")
                merged[label].extend(values)
            cursor = select(response, tool.pagination.cursor)
            if cursor not in (None, ""):
                cursor = page_cursor(cursor)
                if cursor in seen or any(value in cursor for value in credentials):
                    raise RecipeError(
                        "Pagination cursor repeated or reflected credentials"
                    )
                seen.add(cursor)
            merged["_pagination"] = {
                "pages": index + 1,
                "more_available": cursor not in (None, ""),
            }
            ConnectorOutcome(
                text=json.dumps(
                    redact(merged, credentials), ensure_ascii=False, allow_nan=False
                )
            ).validate()
            if cursor in (None, ""):
                break
            url = base + "?" + urlencode({tool.pagination.parameter: cursor})
        return merged

    def typed_body(self, tool, destination, inputs, cursor=""):
        """Materialize the exact reviewed read body; no agent-authored structure."""
        sources = {f"input.{name}": value for name, value in inputs.items()}
        if tool.destination_type == "resource_id":
            sources["destination"] = destination
        if tool.query is not None:
            return {
                "query": tool.query.document,
                "operationName": tool.query.operation,
                "variables": tool.query.variables.materialize(sources),
            }
        body = tool.request_template.materialize(sources)
        if tool.pagination and tool.pagination.location == "body" and cursor:
            body[tool.pagination.parameter] = cursor
        return body

    def typed_read(self, tool, destination, inputs, token, credentials, cancelled):
        """Bounded read-only POSTs: reviewed inputs, exact bodies, honest errors."""
        headers = {
            **dict(self.recipe.request_headers),
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        base = self.recipe.origin + tool.path.replace("{destination}", destination)
        url, cursors = base, []
        merged = {label: [] for label, _ in tool.response}
        pages = tool.pagination.max_pages if tool.pagination else 1
        for index in range(pages):
            self.catalog.active(self.recipe)
            self.store.active(self.account)
            check_cancelled(cancelled)
            if index and identity(
                self.recipe, self.http, token, cancelled, credentials=credentials
            ) != (
                self.account.subject,
                self.account.label,
            ):
                raise AccountError("Account identity changed during pagination")
            body = json.dumps(
                self.typed_body(
                    tool, destination, inputs, cursor=cursors[-1] if cursors else ""
                ),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            response = self.http.request(
                "POST", url, headers=headers, body=body, cancelled=cancelled
            )
            check_cancelled(cancelled)
            self.catalog.active(self.recipe)
            self.store.active(self.account)
            if tool.query is not None:
                response = graphql_result(response, tool.query, credentials)
            else:
                application_error(response, tool.errors, credentials)
            selected = {label: select(response, path) for label, path in tool.response}
            if not tool.pagination:
                return selected
            for label, values in selected.items():
                if not isinstance(values, list):
                    raise RecipeError("Paginated response fields must be arrays")
                merged[label].extend(values)
            cursor = select(response, tool.pagination.cursor)
            if cursor not in (None, ""):
                cursor = page_cursor(cursor)
                if cursor in cursors or any(value in cursor for value in credentials):
                    raise RecipeError(
                        "Pagination cursor repeated or reflected credentials"
                    )
                cursors.append(cursor)
            merged["_pagination"] = {
                "pages": index + 1,
                "more_available": cursor not in (None, ""),
            }
            ConnectorOutcome(
                text=json.dumps(
                    redact(merged, credentials), ensure_ascii=False, allow_nan=False
                )
            ).validate()
            if cursor in (None, ""):
                break
            if tool.pagination.location == "query":
                url = base + "?" + urlencode({tool.pagination.parameter: cursor})
        return merged
