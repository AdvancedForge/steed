"""Resolve modern and legacy engine permissions using an injected decision."""

from PyQt6 import sip
from PyQt6.QtWebEngineCore import QWebEnginePage


class PermissionService:
    """Share request wiring and lifetime checks without choosing a UI or policy."""

    @staticmethod
    def connect_page(page, modern, legacy, desktop, *, modern_available=True):
        if modern_available and hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(modern)
        else:
            page.featurePermissionRequested.connect(
                lambda origin, feature: legacy(page, origin, feature)
            )
        if hasattr(page, "desktopMediaRequested"):
            page.desktopMediaRequested.connect(desktop)

    @staticmethod
    def respond(permission, decide, closing):
        if closing() or not permission.isValid():
            return
        allowed = decide(permission.origin(), permission.permissionType())
        # A modal prompt may run shutdown or invalidate this request.
        if closing() or not permission.isValid():
            return
        if allowed:
            permission.grant()
        else:
            permission.deny()

    @staticmethod
    def respond_legacy(page, origin, feature, decide, closing):
        if closing() or sip.isdeleted(page):
            return
        allowed = decide(origin, feature)
        if closing() or sip.isdeleted(page):
            return
        policy = QWebEnginePage.PermissionPolicy
        page.setFeaturePermission(
            origin,
            feature,
            policy.PermissionGrantedByUser
            if allowed
            else policy.PermissionDeniedByUser,
        )

    @classmethod
    def deny_page(cls, page, closing):
        """Internal task pages have no permission grants yet."""

        def decide(origin, feature):
            return False

        cls.connect_page(
            page,
            lambda permission: cls.respond(permission, decide, closing),
            lambda source, origin, feature: cls.respond_legacy(
                source, origin, feature, decide, closing
            ),
            lambda request: request.cancel(),
        )
        if hasattr(page, "fileSystemAccessRequested"):
            page.fileSystemAccessRequested.connect(lambda request: request.reject())

    @staticmethod
    def feature_name(feature):
        return {
            "MediaAudioCapture": "Microphone",
            "MediaVideoCapture": "Camera",
            "MediaAudioVideoCapture": "Microphone and Camera",
            "DesktopVideoCapture": "Screen Share",
            "DesktopAudioVideoCapture": "Screen Share with Audio",
            "Notifications": "Notifications",
            "Geolocation": "Location",
        }.get(feature.name, "Unknown")
