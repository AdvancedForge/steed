"""Immutable task scopes and thread-safe live authority for engine callbacks."""

import ipaddress
import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import QUrl

from browser.utils import sanitize_profile_name
from services.connector_policy import ConnectorPermission, connector_permissions

SUPPORTED_ACTIONS = frozenset(
    {
        "navigate",
        "inspect",
        "read",
        "attach",
        "edit",
        "scroll",
        "submit",
        "download",
        "upload",
        "artifact",
        "workspace",
        "worker",
        "connector",
        "app",
        "live",
        "visual",
        "computer",
    }
)


def download_destination(value):
    """An exact absolute filename, without aliases or control characters."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4096
        or not value.isprintable()
        or not value.startswith("/")
        or value.startswith("//")
        or os.path.normpath(value) != value
        or value == "/"
    ):
        raise ValueError("Provide an exact absolute destination filename")
    return value


def task_profile_name(selector):
    """Explicit named-profile selector, never a path or a sanitized alias."""
    if not isinstance(selector, str) or not selector.startswith("existing:"):
        raise ValueError("Use private or existing:<profile name>")
    name = selector.removeprefix("existing:")
    if (
        not name
        or len(name) > 128
        or name != sanitize_profile_name(name)
        or not name.isprintable()
        or name == "search"
    ):
        raise ValueError("Choose an existing normal profile other than search")
    return name


class TaskPermissionError(PermissionError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Task permission denied: {code}")


def origin_url(value: str) -> str:
    """Canonical exact origin; never accept credentials, paths, or wildcards."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("Provide an exact HTTP/HTTPS origin")
    url = QUrl(value, QUrl.ParsingMode.StrictMode)
    if (
        not url.isValid()
        or url.scheme() not in ("http", "https")
        or not url.host()
        or url.userName()
        or url.password()
        or url.path() not in ("", "/")
        or url.hasQuery()
        or url.hasFragment()
        or "*" in url.host()
    ):
        raise ValueError("Provide an exact origin without credentials or a path")
    host = url.host().lower()
    raw_host = urlsplit(value).hostname or ""
    if raw_host.startswith("0x") or all(c in "0123456789." for c in raw_host):
        try:
            ipaddress.ip_address(raw_host)
        except ValueError:
            raise ValueError("Use a canonical IP address") from None
    if host.endswith(".") or "%" in host:
        raise ValueError("Use a canonical hostname without a trailing dot or zone")
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        # Chromium interprets these as IPs, unlike Python's ipaddress module.
        if host.startswith("0x") or all(c in "0123456789." for c in host):
            raise ValueError("Use a canonical IP address") from None
        host = host.encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    port = url.port(443 if url.scheme() == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid origin port")
    suffix = "" if port == (443 if url.scheme() == "https" else 80) else f":{port}"
    return f"{url.scheme()}://{host}{suffix}"


def network_needs_permission(origin):
    """DNS names need explicit permission until Qt can pin resolved routes."""
    host = urlsplit(origin).hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not address.is_global


LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localhost", ".arpa")


def everyday_origin(origin):
    """A public website: not an address on this computer or your network."""
    host = (urlsplit(origin).hostname or "").lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host and host != "localhost" and not host.endswith(LOCAL_SUFFIXES)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global


def everyday_path(path):
    """A file in your Downloads folder, not anywhere else in your home."""
    downloads = (Path.home() / "Downloads").resolve()
    try:
        return Path(path).resolve().is_relative_to(downloads)
    except (OSError, ValueError):
        return False


def everyday(scope):
    """What an "Always" access setting may approve without asking: public
    websites and files in Downloads. Your network, this computer and other
    files always ask, whatever the settings."""
    return (
        all(everyday_origin(o) for o in scope.origins + scope.upload_origins)
        and all(everyday_path(p) for p in scope.download_destinations)
        and all(everyday_path(p) for p in scope.upload_sources)
    )


@dataclass(frozen=True)
class TaskScope:
    origins: tuple[str, ...]
    actions: tuple[str, ...] = ("navigate", "inspect", "attach")
    profile: str = "private"
    allow_private_network: bool = False
    duration_seconds: int = 600
    max_operations: int = 100
    max_requests: int = 1000
    max_sessions: int = 1
    download_destinations: tuple[str, ...] = ()
    max_download_bytes: int = 25 * 1024 * 1024
    upload_sources: tuple[str, ...] = ()
    upload_origins: tuple[str, ...] = ()
    max_upload_bytes: int = 5 * 1024 * 1024
    connector_permissions: tuple[ConnectorPermission, ...] = ()

    @classmethod
    def parse(cls, values):
        from services.agent_errors import scope_error

        try:
            return cls._parse(values)
        except ValueError as error:
            raise scope_error(error) from None

    @classmethod
    def _parse(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Provide a task scope object")
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("Unknown scope fields")
        raw = values.get("origins", [])
        if not isinstance(raw, (tuple, list)) or len(raw) > 100:
            raise ValueError("Provide at most 100 exact origins")
        origins = tuple(sorted({origin_url(value) for value in raw}))
        actions = values.get("actions", ("navigate", "inspect", "attach"))
        if (
            not isinstance(actions, (tuple, list))
            or any(not isinstance(action, str) for action in actions)
            or not set(actions) <= SUPPORTED_ACTIONS
        ):
            raise ValueError("Unsupported task action")
        if "computer" in actions:
            # These are dependencies of screenshot-guided browser input, all
            # visible in the normalized approval instead of separate prompts.
            actions = sorted(
                set(actions)
                | {"read", "visual", "live", "navigate", "inspect", "attach"}
            )
        profile = values.get("profile", "private")
        if profile != "private":
            task_profile_name(profile)

        def named(origin):
            try:
                ipaddress.ip_address(urlsplit(origin).hostname)
                return False
            except ValueError:
                return True

        # Named sites need DNS permission in this engine. Include it in the
        # reviewed request by default, rather than rejecting ordinary websites.
        private = values.get("allow_private_network", any(named(o) for o in origins))
        if not isinstance(private, bool):
            raise ValueError("Network permission must be a boolean")
        for origin in origins:
            if not private and network_needs_permission(origin):
                raise ValueError(
                    "Private addresses and DNS hostnames require explicit network permission"
                )
        numbers = {}
        for field, default, maximum in (
            ("duration_seconds", 600, 86400),
            ("max_operations", 100, 10000),
            ("max_requests", 1000, 100000),
            ("max_sessions", 1, 10),
            ("max_download_bytes", 25 * 1024 * 1024, 100 * 1024 * 1024),
            ("max_upload_bytes", 5 * 1024 * 1024, 25 * 1024 * 1024),
        ):
            value = values.get(field, default)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise ValueError(f"Invalid {field}")
            numbers[field] = value
        destinations = values.get("download_destinations", [])
        if not isinstance(destinations, (tuple, list)) or len(destinations) > 20:
            raise ValueError("Provide at most 20 exact download destinations")
        destinations = tuple(sorted({download_destination(p) for p in destinations}))
        if bool(destinations) != ("download" in actions):
            raise ValueError("Download permission requires explicit destinations")
        sources = values.get("upload_sources", [])
        upload_origins = values.get("upload_origins", [])
        if not isinstance(sources, (tuple, list)) or len(sources) > 20:
            raise ValueError("Provide at most 20 exact upload sources")
        if not isinstance(upload_origins, (tuple, list)) or len(upload_origins) > 20:
            raise ValueError("Provide at most 20 exact upload origins")
        sources = tuple(sorted({download_destination(p) for p in sources}))
        upload_origins = tuple(sorted({origin_url(p) for p in upload_origins}))
        if (bool(sources) or bool(upload_origins)) != ("upload" in actions) or (
            "upload" in actions and (not sources or not upload_origins)
        ):
            raise ValueError("Upload permission requires exact sources and origins")
        if not set(upload_origins) <= set(origins):
            raise ValueError("Upload origins must be allowed task origins")
        apps = connector_permissions(values.get("connector_permissions", []))
        if bool(apps) != ("app" in actions):
            raise ValueError(
                "App permission requires exact tools, effects and destinations"
            )
        return cls(
            origins,
            tuple(sorted(set(actions))),
            profile,
            private,
            download_destinations=destinations,
            upload_sources=sources,
            upload_origins=upload_origins,
            connector_permissions=apps,
            **numbers,
        )

    def payload(self):
        return asdict(self)


class LiveGrant:
    """No engine/store calls under the lock; usable from Chromium interception."""

    def __init__(self, scope: TaskScope, *, clock=time.monotonic):
        self.scope = scope
        self._clock = clock
        self.deadline = clock() + scope.duration_seconds
        if not math.isfinite(self.deadline):
            raise ValueError("Invalid grant clock")
        self._lock = threading.Lock()
        self._ended = ""
        self.operations = 0
        self.requests = 0

    def end(self, reason):
        with self._lock:
            if not self._ended:
                self._ended = reason

    def reason(self):
        with self._lock:
            return self._reason()

    def _reason(self):
        if not self._ended and self._clock() >= self.deadline:
            self._ended = "expired"
        return self._ended

    def check(
        self,
        action,
        *,
        url=None,
        profile=None,
        destination=None,
        source=None,
        connector=None,
        tool=None,
        effect=None,
    ):
        with self._lock:
            reason = self._reason()
            if reason:
                raise TaskPermissionError(reason)
            if profile is not None and profile != self.scope.profile:
                raise TaskPermissionError("profile_out_of_scope")
            if action == "app":
                permission = ConnectorPermission(connector, tool, effect, destination)
                if permission not in self.scope.connector_permissions:
                    raise TaskPermissionError("app_out_of_scope")
            elif destination is not None:
                if (
                    action != "download"
                    or destination not in self.scope.download_destinations
                ):
                    raise TaskPermissionError("destination_out_of_scope")
            if source is not None and (
                action != "upload" or source not in self.scope.upload_sources
            ):
                raise TaskPermissionError("source_out_of_scope")
            if action != "open" and action not in self.scope.actions:
                raise TaskPermissionError("action_out_of_scope")
            if url is not None:
                reason = self._url_reason(url)
                if reason:
                    raise TaskPermissionError(reason)
                if action == "upload":
                    try:
                        actual = urlsplit(url)
                        origin = origin_url(f"{actual.scheme}://{actual.netloc}")
                    except ValueError:
                        raise TaskPermissionError(
                            "upload_origin_out_of_scope"
                        ) from None
                    if origin not in self.scope.upload_origins:
                        raise TaskPermissionError("upload_origin_out_of_scope")

    def _url_reason(self, value):
        if value == "about:blank":
            return ""
        url = QUrl(value, QUrl.ParsingMode.StrictMode)
        if (
            not url.isValid()
            or url.scheme() not in ("http", "https")
            or url.userName()
            or url.password()
        ):
            return "unsupported_url"
        url.setPath("")
        url.setQuery(None)
        url.setFragment(None)
        try:
            origin = origin_url(url.toString())
        except ValueError:
            return "unsupported_url"
        if origin not in self.scope.origins:
            return "origin_out_of_scope"
        if not self.scope.allow_private_network and network_needs_permission(origin):
            return "network_out_of_scope"
        return ""

    def consume_operation(self):
        with self._lock:
            reason = self._reason()
            if reason:
                raise TaskPermissionError(reason)
            if self.operations >= self.scope.max_operations:
                self._ended = "operation_budget_exhausted"
                raise TaskPermissionError(self._ended)
            self.operations += 1

    def request(self, url, method=b"GET", *, submission=False):
        with self._lock:
            reason = self._reason()
            if not reason and "navigate" not in self.scope.actions:
                reason = "action_out_of_scope"
            if not reason and submission and "submit" not in self.scope.actions:
                reason = "action_out_of_scope"
            if not reason and method not in (
                (b"GET", b"POST") if submission else (b"GET", b"HEAD")
            ):
                reason = "method_out_of_scope"
            if not reason:
                reason = self._url_reason(url)
            if not reason:
                if self.requests >= self.scope.max_requests:
                    self._ended = reason = "request_budget_exhausted"
                else:
                    self.requests += 1
            return reason

    def navigation(self, url):
        with self._lock:
            return self._reason() or (
                "action_out_of_scope"
                if "navigate" not in self.scope.actions
                else self._url_reason(url)
            )
