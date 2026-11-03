"""The codec engine swap backs up, refuses mismatches and restores exactly."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_codecs as codecs


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class SwapTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.wheel, self.stage = root / "Qt6", root / "stage"
        for name in codecs.LIBRARIES:
            write(self.wheel / "lib" / name, f"pypi {name}")
            # The install tree has versioned files behind the .so.6 links.
            write(self.stage / "lib" / f"{name}.11.2", f"built {name}")
            (self.stage / "lib" / name).symlink_to(f"{name}.11.2")
        write(self.wheel / "libexec" / "QtWebEngineProcess", "pypi process")
        write(self.stage / "libexec" / "QtWebEngineProcess", "built process")
        for name in codecs.RESOURCES:
            write(self.wheel / "resources" / name, f"pypi {name}")
            write(self.stage / "resources" / name, f"built {name}")
        core = self.stage / "lib" / codecs.LIBRARIES[0]
        write(
            self.stage / codecs.MARKER,
            json.dumps({"version": "6.11.2", "core": codecs._digest(core.resolve())}),
        )
        patches = (
            patch.object(codecs, "qt_version", return_value="6.11.2"),
            patch.object(codecs, "_exports", return_value={"symbol"}),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def contents(self):
        return {
            path.relative_to(self.wheel).as_posix(): path.read_text()
            for path in self.wheel.rglob("*")
            if path.is_file() and codecs.BACKUP not in path.parts
        }

    def test_install_and_restore(self):
        before = self.contents()
        codecs.install(self.stage, self.wheel)
        after = self.contents()
        self.assertEqual(
            after["lib/libQt6WebEngineCore.so.6"], "built libQt6WebEngineCore.so.6"
        )
        self.assertEqual(after["libexec/QtWebEngineProcess"], "built process")
        self.assertTrue(codecs.using_build(self.wheel))
        # Installing again keeps the PyPI originals, not the build, as backup.
        codecs.install(self.stage, self.wheel)
        codecs.restore(self.wheel)
        after_restore = self.contents()
        self.assertEqual(after_restore, before)
        self.assertFalse((self.wheel / codecs.BACKUP).exists())
        self.assertFalse(codecs.using_build(self.wheel))

    def test_pip_reinstall_is_detected_and_reinstall_keeps_new_pypi_files(self):
        codecs.install(self.stage, self.wheel)
        # pip puts its own files back but leaves Steed's marker and backup.
        write(self.wheel / "lib" / codecs.LIBRARIES[0], "pypi again")
        self.assertFalse(codecs.using_build(self.wheel))
        codecs.install(self.stage, self.wheel)
        saved = self.wheel / codecs.BACKUP / "lib" / codecs.LIBRARIES[0]
        self.assertEqual(saved.read_text(), "pypi again")

    def test_refuses_other_versions_missing_functions_and_unfinished_builds(self):
        with patch.object(codecs, "qt_version", return_value="6.12.0"):
            with self.assertRaises(SystemExit):
                codecs.install(self.stage, self.wheel)
        exports = {"symbol", "only_in_pypi"}
        with patch.object(
            codecs,
            "_exports",
            side_effect=lambda path: (
                exports if "pypi" in path.read_text() else {"symbol"}
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "lacks 1 functions"):
                codecs.install(self.stage, self.wheel)
        self.assertEqual(
            (self.wheel / "lib" / codecs.LIBRARIES[0]).read_text(),
            f"pypi {codecs.LIBRARIES[0]}",
        )
        (self.stage / codecs.MARKER).unlink()
        with self.assertRaisesRegex(SystemExit, "No finished build"):
            codecs.install(self.stage, self.wheel)

    def test_missing_requirements_name_packages(self):
        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as run,
        ):
            run.return_value.returncode = 1
            packages, problems = codecs.missing_requirements(self.stage)
        self.assertIn("bison", packages)
        self.assertIn("libnss3-dev", packages)
        self.assertTrue(any("Node.js" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
