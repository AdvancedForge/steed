"""One dispatch ticket per task page, shared with Chromium's network callback."""

import threading
import time

from PyQt6.QtCore import QUrl

from services.task_policy import TaskPermissionError


def journal_origin(value):
    """Journal routes without query, path, fragments, or reflected form values."""
    url = QUrl(value)
    if url.scheme() not in ("http", "https"):
        return "about:blank" if value == "about:blank" else ""
    url.setUserName("")
    url.setPassword("")
    url.setPath("")
    url.setQuery(None)
    url.setFragment(None)
    return url.toString()


class SubmissionTicket:
    """Never grants general POST authority or permits replay through redirects."""

    def __init__(self, grant, *, clock=time.monotonic):
        self.grant = grant
        self.clock = clock
        self.lock = threading.Lock()
        self.destination = None
        self.method = None
        self.deadline = 0
        self.navigation_used = False
        self.request_used = False
        self.request_authorized = False
        self.redirected = False

    @staticmethod
    def route(value, method):
        url = QUrl(value)
        url.setFragment(None)
        # HTML GET submission replaces the action's query with native form data.
        if method == b"GET":
            url.setQuery(None)
        return url.toString(QUrl.ComponentFormattingOption.FullyEncoded)

    def arm(self, url, method):
        self.grant.check("submit", url=url)
        self.grant.check("navigate", url=url)
        method = method.encode("ascii")
        if method not in (b"GET", b"POST"):
            raise TaskPermissionError("method_out_of_scope")
        with self.lock:
            self.destination = self.route(url, method)
            self.method = method
            self.deadline = self.clock() + 5
            self.navigation_used = self.request_used = False
            self.request_authorized = self.redirected = False

    def clear(self):
        with self.lock:
            self.destination = None

    def _matches(self, url, method):
        return (
            self.destination is not None
            and self.clock() < self.deadline
            and method == self.method
            and self.route(url, method) == self.destination
        )

    def navigation(self, url, main_frame):
        with self.lock:
            if (
                not main_frame
                or self.navigation_used
                or not self._matches(url, self.method)
            ):
                return "submission_not_authorized"
            self.navigation_used = True
            return ""

    def redirect(self, main_frame):
        with self.lock:
            # Qt applies interceptor blocks to followed redirects asynchronously,
            # so a 307/308 can resend the body before the block lands. The
            # navigation gate runs before the redirect is followed but cannot
            # see its method, so no redirect is followed after a POST.
            if (
                main_frame
                and self.destination is not None
                and self.method == b"POST"
                and self.request_used
            ):
                self.redirected = True
                return "submission_redirect_not_followed"
            return ""

    def request(self, url, method, main_frame):
        with self.lock:
            # Qt retains FormSubmitted on HTTP redirects. Subsequent GETs use
            # ordinary navigation authority; no POST/body replay is allowed.
            # A second actual form navigation is rejected by navigation().
            if (
                self.destination is not None
                and self.request_authorized
                and main_frame
                and method == b"GET"
            ):
                return self.grant.request(url, method)
            if not main_frame or self.request_used or not self._matches(url, method):
                return "submission_not_authorized"
            self.request_used = True
            reason = self.grant.request(url, method, submission=True)
            self.request_authorized = not reason
            return reason

    def authorized(self):
        with self.lock:
            return self.request_authorized
