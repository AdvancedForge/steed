"""Permission regressions for both old and new Qt WebEngine APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWidgets import QMessageBox

from browser_window import BrowserWindow, QWebEnginePermission


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.window = Mock(spec=BrowserWindow)
        self.window._closing = False
        self.origin = QUrl("https://example.org")

    def test_legacy_signal_replies_to_requesting_page(self):
        page = SimpleNamespace(
            featurePermissionRequested=Mock(),
            fullScreenRequested=Mock(),
        )
        with patch("browser_window.QWebEnginePermission", None):
            BrowserWindow._connect_page_signals(self.window, page)
        callback = page.featurePermissionRequested.connect.call_args.args[0]
        feature = QWebEnginePage.Feature.MediaAudioCapture
        callback(self.origin, feature)
        self.window._on_feature_permission_requested.assert_called_once_with(
            page, self.origin, feature
        )
        page.fullScreenRequested.connect.assert_called_once()

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_modern_signal_keeps_request_api_and_media_picker(self):
        page = Mock()
        BrowserWindow._connect_page_signals(self.window, page)
        page.permissionRequested.connect.assert_called_once_with(
            self.window._on_permission_requested
        )
        page.featurePermissionRequested.connect.assert_not_called()
        page.desktopMediaRequested.connect.assert_called_once_with(
            self.window._on_desktop_media_requested
        )

    def test_legacy_acceptance_and_denial_use_origin_and_feature(self):
        page = Mock()
        feature = QWebEnginePage.Feature.MediaVideoCapture
        for allowed, policy in (
            (True, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser),
            (False, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser),
        ):
            with self.subTest(allowed=allowed):
                page.reset_mock()
                self.window._ask_permission.return_value = allowed
                with patch("browser_window.sip.isdeleted", return_value=False):
                    BrowserWindow._on_feature_permission_requested(
                        self.window, page, self.origin, feature
                    )
                page.setFeaturePermission.assert_called_once_with(
                    self.origin, feature, policy
                )

    def test_modern_acceptance_and_denial_use_request_object(self):
        for allowed in (True, False):
            with self.subTest(allowed=allowed):
                permission = Mock()
                self.window._ask_permission.return_value = allowed
                BrowserWindow._on_permission_requested(self.window, permission)
                self.assertEqual(permission.grant.call_count, int(allowed))
                self.assertEqual(permission.deny.call_count, int(not allowed))

    def test_invalid_request_is_ignored(self):
        permission = Mock()
        permission.isValid.return_value = False
        BrowserWindow._on_permission_requested(self.window, permission)
        self.window._ask_permission.assert_not_called()
        permission.grant.assert_not_called()
        permission.deny.assert_not_called()

    def test_page_deleted_during_prompt_receives_no_response(self):
        page = Mock()
        with patch("browser_window.sip.isdeleted", side_effect=[False, True]):
            BrowserWindow._on_feature_permission_requested(
                self.window, page, self.origin, QWebEnginePage.Feature.Notifications
            )
        page.setFeaturePermission.assert_not_called()

    def test_prompt_names_both_enum_types_and_defaults_to_no(self):
        types = [QWebEnginePage.Feature]
        if QWebEnginePermission is not None:
            types.append(QWebEnginePermission.PermissionType)
        for enum_type in types:
            with self.subTest(enum_type=enum_type):
                with patch.object(
                    QMessageBox, "question", return_value=QMessageBox.StandardButton.No
                ) as question:
                    allowed = BrowserWindow._ask_permission(
                        self.window, self.origin, enum_type.MediaAudioCapture
                    )
                self.assertFalse(allowed)
                self.assertEqual(
                    question.call_args.args[2],
                    "https://example.org requests Microphone. Allow?",
                )
                self.assertEqual(
                    question.call_args.args[4], QMessageBox.StandardButton.No
                )


if __name__ == "__main__":
    unittest.main()
