"""Real process-boundary cookie persistence check using caller-owned test data."""

import argparse
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QDateTime, QTimer, QUrl
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_window import BrowserWindow  # noqa: E402
from config import Config  # noqa: E402


def check(data, phase):
    with patch("pathlib.Path.home", return_value=data):
        config = Config()
        config.set(
            "home_page",
            (Path(__file__).resolve().parents[1] / "codec_test.html").as_uri(),
        )
        app = QApplication(["profile-restart"])
        app.setQuitOnLastWindowClosed(False)
        window = BrowserWindow()
        sharing = window.profile_manager.registry.sharing
        result = {"passed": False}
        properties = (
            window.profile_manager.global_profile.isOffTheRecord(),
            window.profile_manager.global_profile.persistentStoragePath(),
            window.profile_manager.global_profile.persistentCookiesPolicy().name,
        )

        def finish():
            window.close()
            app.quit()

        def inspect():
            if sharing.global_cookies and sharing.observed["default"]:
                result["passed"] = not sharing.local(
                    "default", "restart-fixture.example"
                )
                finish()

        timer = QTimer()
        timer.timeout.connect(inspect)
        timer.start(20)
        if phase == "write":
            cookie = QNetworkCookie(b"persistent", b"baseline")
            cookie.setDomain(".restart-fixture.example")
            cookie.setPath("/")
            cookie.setExpirationDate(QDateTime.currentDateTimeUtc().addDays(1))
            window.profile_manager.global_profile.cookieStore().setCookie(
                cookie, QUrl("https://restart-fixture.example")
            )
        QTimer.singleShot(8000, finish)
        app.exec()
        timer.stop()
        sip.delete(app)
        print(
            "Persistence diagnostic:",
            phase,
            properties,
            "loaded",
            len(sharing.global_cookies),
            len(sharing.observed["default"]),
            flush=True,
        )
        for directory in (
            config.config_dir / "global_profile" / "storage",
            config.profiles_dir / "default" / "storage",
        ):
            for db_path in (directory / "Cookies", directory / "Network" / "Cookies"):
                if db_path.exists():
                    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
                        print(
                            "Fixture cookie metadata:",
                            db_path,
                            db.execute(
                                "select name, host_key, expires_utc, is_persistent from cookies"
                            ).fetchall(),
                            flush=True,
                        )
        if not result["passed"]:
            raise RuntimeError(f"Cookie persistence phase {phase} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--phase", choices=("write", "read"), required=True)
    args = parser.parse_args()
    check(args.data, args.phase)
