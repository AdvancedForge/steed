"""Real KeePassXC transport/save/retrieve/lock check using a temporary test vault.

This seeds a fixture association in that vault; it does not approve a real vault
or exercise KeePassXC's interactive, one-time pairing dialog. No desktop window
is opened and no existing KeePassXC configuration is used.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nacl.public import PrivateKey  # noqa: E402

from password_manager import KeePassXCClient, PasswordManagerError, encode  # noqa: E402

FIXTURE_PASSWORD = "temporary-smoke-vault-passphrase"


def run(binary):
    cli = binary.with_name("keepassxc-cli")
    if not binary.is_file() or not cli.is_file():
        raise SystemExit("This check needs both keepassxc and keepassxc-cli.")
    # Unix-domain socket paths have a small platform length limit.
    with tempfile.TemporaryDirectory(prefix="spw-") as directory:
        root = Path(directory)
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        for key, name in (
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_RUNTIME_DIR", "runtime"),
        ):
            location = root / name
            location.mkdir(mode=0o700)
            env[key] = str(location)

        def command(args, password=FIXTURE_PASSWORD + "\n"):
            return subprocess.run(
                [str(cli), *args],
                input=password,
                text=True,
                env=env,
                capture_output=True,
                check=True,
                timeout=30,
            ).stdout

        original = root / "empty.kdbx"
        command(["db-create", "-p", str(original)], (FIXTURE_PASSWORD + "\n") * 2)
        document = ET.fromstring(command(["export", str(original)]))
        metadata = document.find("Meta")
        custom = metadata.find("CustomData")
        if custom is None:
            custom = ET.SubElement(metadata, "CustomData")
        id_key = encode(bytes(PrivateKey.generate().public_key))
        item = ET.SubElement(custom, "Item")
        ET.SubElement(item, "Key").text = "KPXC_BROWSER_STEED_SMOKE"
        ET.SubElement(item, "Value").text = id_key
        xml_path = root / "fixture.xml"
        ET.ElementTree(document).write(xml_path, encoding="utf-8", xml_declaration=True)
        vault = root / "fixture.kdbx"
        command(
            ["import", "-p", str(xml_path), str(vault)], (FIXTURE_PASSWORD + "\n") * 2
        )
        # This permission applies only to the freshly created synthetic vault.
        ini = root / "keepassxc.ini"
        ini.write_text(
            "[Browser]\nEnabled=true\nUpdateBinaryPath=false\nAlwaysAllowAccess=true\n"
            "\n[General]\nConfigVersion=2\n"
        )
        app = subprocess.Popen(
            [
                str(binary),
                "--config",
                str(ini),
                "--localconfig",
                str(root / "local.ini"),
                "--pw-stdin",
                str(vault),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        app.stdin.write(FIXTURE_PASSWORD + "\n")
        app.stdin.flush()
        runtime = root / "runtime"
        paths = [
            runtime
            / "app/org.keepassxc.KeePassXC/org.keepassxc.KeePassXC.BrowserServer",
            runtime / "org.keepassxc.KeePassXC.BrowserServer",
        ]
        try:
            deadline = time.monotonic() + 15
            while not any(path.exists() for path in paths):
                if app.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Temporary KeePassXC could not start its browser socket."
                    )
                time.sleep(0.05)
            # The database opens asynchronously after the socket starts.
            memory = {}
            while True:
                try:
                    with KeePassXCClient(None, memory, paths, timeout=3) as client:
                        db_hash = client.request(
                            "get-databasehash", trigger_unlock=False
                        )["hash"]
                        memory[db_hash] = {"id": "STEED_SMOKE", "key": id_key}
                        break
                except PasswordManagerError:
                    if time.monotonic() >= deadline:
                        raise
                    if app.poll() is not None:
                        raise RuntimeError(
                            "Temporary KeePassXC exited during startup."
                        ) from None
                    time.sleep(0.1)
            with KeePassXCClient(None, memory, paths, timeout=5) as client:
                assert (
                    client.execute("get-logins", {"url": "https://fixture.example/"})
                    == []
                )
                client.execute(
                    "set-login",
                    {
                        "url": "https://fixture.example/",
                        "submitUrl": "https://fixture.example/login",
                        "login": "smoke-user",
                        "password": "smoke-entry-secret",
                    },
                )
                entries = client.execute(
                    "get-logins", {"url": "https://fixture.example/"}
                )
                assert len(entries) == 1
                assert entries[0]["login"] == "smoke-user"
                assert entries[0]["password"] == "smoke-entry-secret"
                client.request("lock-database")
                try:
                    client.request(
                        "get-logins",
                        trigger_unlock=False,
                        url="https://fixture.example/",
                    )
                except PasswordManagerError:
                    pass
                else:
                    raise AssertionError("Locked vault returned credentials")
            print("Real KeePassXC encrypted save/retrieve and vault-lock check passed.")
        finally:
            app.terminate()
            try:
                app.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait()
            app.stdin.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keepassxc", default=shutil.which("keepassxc"))
    args = parser.parse_args()
    if not args.keepassxc:
        raise SystemExit(
            "Install KeePassXC to run this optional real-vault smoke check."
        )
    run(Path(args.keepassxc).resolve())
