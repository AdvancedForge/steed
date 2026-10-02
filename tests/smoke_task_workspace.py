"""Trusted workspace picker actions with synthetic files and a UI screenshot."""

import base64
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import sip  # noqa: E402
from test_task_workspace import WorkspaceTests  # noqa: E402

from ui.task_dialog import TaskDialog  # noqa: E402


def check():
    WorkspaceTests.setUpClass()
    case = WorkspaceTests()
    case.setUp()
    dialog = None
    try:
        item = case.file("attachment.pdf", b"%PDF-SYNTHETIC\x00\xff")
        manifest = case.package()
        case.call("workspace.stage_extension", manifest_id=manifest.file_id)
        source = case.root / "user-selected.csv"
        source.write_bytes(b"SYNTHETIC_USER_SELECTED_INPUT\x00\xff")
        dialog = TaskDialog(case.service)
        dialog.show()
        dialog.table.selectRow(0)
        dialog.refresh_activity()
        with patch(
            "ui.task_dialog.QFileDialog.getOpenFileName", return_value=(str(source), "")
        ):
            dialog.share_workspace_file()
        shared = next(f for f in case.call("workspace.list") if f.name == source.name)
        assert shared.source == "user_shared"
        read = case.call("workspace.read", file_id=shared.file_id)
        assert base64.b64decode(read["data_base64"]) == source.read_bytes()
        dialog.workspace_files.setCurrentIndex(
            dialog.workspace_files.findData(item.file_id)
        )
        target = case.root / "exported.pdf"
        with patch(
            "ui.task_dialog.QFileDialog.getSaveFileName", return_value=(str(target), "")
        ):
            dialog.export_workspace_file()
        assert target.read_bytes() == b"%PDF-SYNTHETIC\x00\xff"
        assert (
            "Requested access (not granted)" in dialog.workspace_preview.toPlainText()
        )
        assert "SEND:" in dialog.workspace_preview.toPlainText()
        assert "execution permission required" in dialog.workspace_preview.toPlainText()
        for index in range(dialog.panels.count()):
            if dialog.panels.tabText(index) == "Workspace":
                dialog.panels.setCurrentIndex(index)
        case.app.processEvents()
        assert dialog.grab().save("/tmp/steed-task-workspace.png")
        print(
            "Workspace user-picker actions, synthetic shared-input reads, binary "
            "export, inert package review and screenshot passed. No real files used."
        )
    finally:
        if dialog is not None:
            dialog.close()
            sip.delete(dialog)
        case.tearDown()


if __name__ == "__main__":
    check()
