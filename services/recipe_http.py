"""Public-address-pinned HTTPS for user-reviewed recipes, with no proxy or redirects."""

import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from services.app_accounts import AccountError
from services.app_oauth import ApiError
from services.connector_recipes import (
    page_cursor,
    query_parameter,
    recipe_endpoint,
    recipe_origin,
    request_path,
)
from services.task_workspace import unique_object


def check_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise AccountError("Connection or app operation cancelled")


class RecipeHttp:
    def __init__(self, origin, *, token_endpoint="", query_parameters=None):
        self.origin = recipe_origin(origin)
        self.token_endpoint = recipe_endpoint(token_endpoint) if token_endpoint else ""
        self.query_parameters = {
            request_path(path): frozenset(query_parameter(p) for p in parameters)
            for path, parameters in (query_parameters or {}).items()
        }

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        parts = urlsplit(url)
        token_request = bool(
            self.token_endpoint and method == "POST" and url == self.token_endpoint
        )
        target = parts.path
        if parts.query and not token_request:
            query = parse_qs(
                parts.query,
                strict_parsing=True,
                max_num_fields=1,
                keep_blank_values=True,
            )
            if len(query) != 1:
                raise ValueError("Use one reviewed pagination query")
            parameter, values = next(iter(query.items()))
            if (
                parameter not in self.query_parameters.get(parts.path, ())
                or len(values) != 1
                or parts.query != urlencode({parameter: page_cursor(values[0])})
            ):
                raise ValueError("Query differs from the reviewed pagination parameter")
            target += "?" + parts.query
        if (
            not token_request
            and (method != "GET" or body is not None or url != self.origin + target)
            or not parts.path.startswith("/")
            or parts.path.startswith("//")
        ):
            raise ValueError(
                "Recipe request differs from its reviewed origin or method"
            )
        headers = headers or {}
        if token_request:
            if (
                headers != {"Content-Type": "application/x-www-form-urlencoded"}
                or not isinstance(body, bytes)
                or not 1 <= len(body) <= 32768
                or not body.isascii()
            ):
                raise ValueError("Use a bounded trusted token exchange form")
        elif set(headers) - {"Authorization", "Accept"}:
            raise ValueError("Unsupported recipe headers")
        check_cancelled(cancelled)
        deadline = time.monotonic() + 10
        connection, channel, response = None, None, None
        try:
            addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
            if (
                not addresses
                or len(addresses) > 32
                or any(
                    not ipaddress.ip_address(a[4][0]).is_global
                    or ipaddress.ip_address(a[4][0]).is_multicast
                    or ipaddress.ip_address(a[4][0]).is_reserved
                    or ipaddress.ip_address(a[4][0]).is_unspecified
                    or getattr(ipaddress.ip_address(a[4][0]), "is_site_local", False)
                    or getattr(ipaddress.ip_address(a[4][0]), "ipv4_mapped", None)
                    is not None
                    for a in addresses
                )
            ):
                raise ApiError()
            check_cancelled(cancelled)
            if time.monotonic() >= deadline:
                raise ApiError()
            family, kind, protocol, _, address = addresses[0]
            channel = socket.socket(family, kind, protocol)
            channel.settimeout(max(0.01, deadline - time.monotonic()))
            channel.connect(address)  # Numeric validated address: no second DNS lookup.
            context = ssl.create_default_context()
            context.set_alpn_protocols(["http/1.1"])
            channel = context.wrap_socket(channel, server_hostname=parts.hostname)
            connection = http.client.HTTPConnection(parts.hostname, 443)
            connection.sock = channel
            check_cancelled(cancelled)
            if time.monotonic() >= deadline:
                raise ApiError()
            channel.settimeout(max(0.01, deadline - time.monotonic()))
            connection.request(
                method,
                target,
                headers={
                    "Host": parts.hostname,
                    "Accept": "application/json",
                    **headers,
                },
                body=body,
            )
            # HTTPConnection.getresponse() closes its socket immediately for
            # Connection: close / HTTP 1.0. Keep ownership until the bounded body
            # read finishes so its timeout remains adjustable between reads.
            response = http.client.HTTPResponse(channel, method=method)
            response.begin()
            if (
                response.status != 200
                or response.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise ApiError(response.status)
            raw = bytearray()
            while True:
                check_cancelled(cancelled)
                if time.monotonic() >= deadline:
                    raise ApiError()
                channel.settimeout(max(0.01, deadline - time.monotonic()))
                data = response.read1(min(8192, 128 * 1024 + 1 - len(raw)))
                if not data:
                    break
                raw.extend(data)
                if len(raw) > 128 * 1024:
                    raise ApiError()
            check_cancelled(cancelled)
            return json.loads(
                raw,
                object_pairs_hook=unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError("Nonfinite response")
                ),
            )
        except (OSError, ValueError, http.client.HTTPException, RecursionError):
            raise ApiError() from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            elif channel is not None:
                channel.close()
