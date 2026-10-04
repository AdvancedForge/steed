"""Native OAuth for immutable recipes; registration and tokens stay in the keyring."""

import json
import math
import threading
import time
from urllib.parse import urlencode

from services.app_accounts import AccountError
from services.app_oauth import desktop_authorization
from services.recipe_http import RecipeHttp, check_cancelled
from services.task_workspace import unique_object


def opaque(value, maximum=8192):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not all(33 <= ord(c) < 127 for c in value)
    ):
        raise AccountError("The service returned invalid OAuth credentials")
    return value


def client_configuration(recipe, store, path):
    try:
        if path is not None:
            raw = path.read_bytes()
            if len(raw) > 16384:
                raise ValueError("Client setup is too large")
            value = json.loads(raw, object_pairs_hook=unique_object)
        else:
            saved = store.client_configuration(recipe.provider)
            if saved.get("recipe_sha256") != recipe.sha256:
                raise ValueError("Client setup differs from the recipe")
            value = {k: v for k, v in saved.items() if k != "recipe_sha256"}
        if (
            not isinstance(value, dict)
            or not {"client_id"} <= value.keys()
            or set(value) - {"client_id", "client_secret"}
        ):
            raise ValueError("Unsupported client setup fields")
        result = {"client_id": opaque(value["client_id"], 1024)}
        if "client_secret" in value:
            result["client_secret"] = opaque(value["client_secret"], 1024)
    except (KeyError, TypeError, ValueError, OSError, RecursionError):
        raise AccountError(
            "Choose a native OAuth client JSON containing client_id and optionally client_secret; endpoints come only from the reviewed recipe"
        ) from None
    check = {"recipe_sha256": recipe.sha256, **result}
    if path is not None:
        store.client_configuration(recipe.provider, check)
    return result


def token_response(result, scopes):
    from services.recipe_connector import bearer_token

    if (
        not isinstance(result, dict)
        or result.get("error")
        or not isinstance(result.get("token_type"), str)
        or result["token_type"].lower() != "bearer"
    ):
        raise AccountError("The service returned an invalid OAuth response")
    access = bearer_token(result.get("access_token"))
    expires = result.get("expires_in")
    returned = result.get("scope")
    if (
        type(expires) is not int
        or not 1 <= expires <= 86400
        or returned is not None
        and (
            not isinstance(returned, str)
            or len(returned) > 4096
            or set(returned.split()) != set(scopes)
        )
    ):
        raise AccountError(
            "The service did not return the reviewed scopes and a bounded token lifetime"
        )
    return access, time.time() + expires


def token_http(recipe, http=None):
    from services.recipe_plans import transport_path

    return http or RecipeHttp(
        recipe.origin,
        token_endpoint=recipe.authentication.token_endpoint,
        request_headers=dict(recipe.request_headers)
        if recipe.request_headers
        else None,
        query_parameters={
            tool.path: {
                t.pagination.parameter
                for t in recipe.tools
                if t.path == tool.path and t.pagination
            }
            for tool in recipe.tools
            if tool.pagination
        },
        post_rules=[
            (
                transport_path(step.path),
                step.idempotency_header,
                bool(step.request_template),
            )
            for tool in recipe.tools
            if tool.effect != "read"
            for step in (tool.steps or (tool,))
        ],
        read_posts=[
            tool.path
            for tool in recipe.tools
            if tool.method == "POST" and tool.effect == "read"
        ]
        + [
            tool.pagination.continuation.path
            for tool in recipe.tools
            if tool.pagination and tool.pagination.continuation
        ]
        + ([recipe.identity_path] if recipe.identity_request is not None else []),
    )


def exchange(recipe, http, parameters, cancelled):
    check_cancelled(cancelled)
    return http.request(
        "POST",
        recipe.authentication.token_endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(parameters).encode("ascii"),
        cancelled=cancelled,
    )


def connect_recipe_oauth(
    recipe, catalog, store, path, capabilities, cancelled, *, http=None, authorize=None
):
    from services.recipe_connector import identity

    catalog.active(recipe)
    if (
        recipe.authentication.kind != "oauth_pkce"
        or not capabilities
        or not set(capabilities) <= {t.name for t in recipe.tools}
    ):
        raise AccountError("Choose supported OAuth recipe tools")
    store.secrets.check(unlock=True)
    client = client_configuration(recipe, store, path)
    http = token_http(recipe, http)
    if authorize is None:

        def authorize(endpoint, client_id, scopes, cancelled):
            return desktop_authorization(
                endpoint, client_id, scopes, cancelled, google_options=False
            )

    auth = recipe.authentication
    code, verifier, redirect = authorize(
        auth.authorization_endpoint, client["client_id"], auth.scopes, cancelled
    )
    catalog.active(recipe)
    result = exchange(
        recipe,
        http,
        {
            **client,
            "grant_type": "authorization_code",
            "code": opaque(code),
            "code_verifier": opaque(verifier, 128),
            "redirect_uri": redirect,
        },
        cancelled,
    )
    access, expires = token_response(result, auth.scopes)
    refresh = opaque(result["refresh_token"]) if "refresh_token" in result else None
    subject, label = identity(
        recipe,
        http,
        access,
        cancelled,
        credentials=(refresh, client.get("client_secret"), code, verifier),
    )
    if any(
        v and (v in subject or v in label)
        for v in (refresh, client.get("client_secret"))
    ):
        raise AccountError("The service reflected a credential as account identity")
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
            "kind": "oauth_pkce",
            **client,
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": expires,
            "scopes": list(auth.scopes),
        },
        cancelled,
    )


class RecipeOAuthAccess:
    """Serialize refresh and persist rotated tokens before publishing any data."""

    def __init__(self, recipe, catalog, account, store, http):
        self.recipe, self.catalog, self.account, self.store, self.http = (
            recipe,
            catalog,
            account,
            store,
            http,
        )
        self.lock, self.cached = threading.Lock(), None

    def credential(self, cancelled):
        from services.recipe_connector import bearer_token, identity

        with self.lock:
            self.catalog.active(self.recipe)
            self.store.active(self.account)
            check_cancelled(cancelled)
            first = self.cached is None
            saved = self.cached or self.store.secrets.load(self.account.account_id)
            auth = self.recipe.authentication
            if (
                saved.get("kind") != "oauth_pkce"
                or saved.get("provider") != self.recipe.provider
                or saved.get("recipe_sha256") != self.recipe.sha256
                or saved.get("subject") != self.account.subject
                or saved.get("scopes") != list(auth.scopes)
            ):
                raise AccountError(
                    "OAuth credentials differ from this recipe or account"
                )
            access = bearer_token(saved.get("access_token"))
            expires = saved.get("expires_at")
            if type(expires) not in (int, float) or not math.isfinite(expires):
                raise AccountError(
                    "Invalid OAuth token lifetime; reconnect this service"
                )
            refresh = saved.get("refresh_token")
            if refresh is None:
                if expires <= time.time() + 60:
                    raise AccountError(
                        "This service did not grant refresh access; reconnect it"
                    )
            elif first or expires <= time.time() + 60:
                parameters = {
                    "client_id": opaque(saved.get("client_id"), 1024),
                    "refresh_token": opaque(refresh),
                    "grant_type": "refresh_token",
                }
                if "client_secret" in saved:
                    parameters["client_secret"] = opaque(saved["client_secret"], 1024)
                result = exchange(self.recipe, self.http, parameters, cancelled)
                access, expires = token_response(result, auth.scopes)
                refreshed = {**saved, "access_token": access, "expires_at": expires}
                if "refresh_token" in result:
                    refreshed["refresh_token"] = opaque(result["refresh_token"])
                if identity(
                    self.recipe,
                    self.http,
                    access,
                    cancelled,
                    credentials=tuple(
                        packet.get(field)
                        for packet in (saved, refreshed)
                        for field in ("access_token", "refresh_token", "client_secret")
                    ),
                ) != (
                    self.account.subject,
                    self.account.label,
                ):
                    raise AccountError(
                        "OAuth account identity changed; reconnect this service"
                    )
                self.catalog.active(self.recipe)
                check_cancelled(cancelled)
                self.store.save_credential(self.account, refreshed)
                saved = refreshed
            self.catalog.active(self.recipe)
            self.store.active(self.account)
            check_cancelled(cancelled)
            self.cached = saved
            return saved
