#!/usr/bin/env python3
"""Register this checkout in the Linux application menu without root access."""

import argparse
import os
from pathlib import Path


def desktop_entry(project: Path) -> str:
    launcher = str(project / "steed")
    if any(character in launcher for character in "\n\r="):
        raise ValueError(
            "The project path contains unsupported desktop-entry characters"
        )
    # Exec quoting happens after desktop string escaping; this is not shell syntax.
    argument = "".join(
        "\\" + character if character in '\\"`$' else character
        for character in launcher
    )
    argument = argument.replace("\\", "\\\\").replace("%", "%%")
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Steed\n"
        "Comment=Browse the web with separate profiles and KeePassXC passwords\n"
        f'Exec="{argument}"\n'
        "Icon=web-browser\n"
        "Terminal=false\n"
        "Categories=Network;WebBrowser;\n"
        "Keywords=browser;web;internet;\n"
        "StartupNotify=false\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    parser.add_argument(
        "--applications-dir", type=Path, default=data_home / "applications"
    )
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    if not os.access(project / "steed", os.X_OK):
        parser.error("steed is not executable; run chmod +x steed")
    if not os.access(project / ".venv/bin/python", os.X_OK):
        parser.error("Create .venv and install requirements.txt first (see README.md)")
    contents = desktop_entry(project)
    args.applications_dir.mkdir(parents=True, exist_ok=True)
    destination = args.applications_dir / "steed.desktop"
    destination.write_text(contents, encoding="utf-8")
    print(f"Installed {destination}. Open Steed from your applications menu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
