"""Keep late Chromium writes separate from retired and newly created profiles."""

import json
from uuid import uuid4

from utils import sanitize_profile_name, save_json

MANIFEST = ".steed_profile.json"


class ProfileStorage:
    """Legacy stores stay in place; fresh identities use unique engine paths."""

    def __init__(self, config):
        self.config = config
        self.index = config.config_dir / "retired_profiles.json"
        try:
            raw = json.loads(self.index.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = []
        if not isinstance(raw, list) or any(
            not isinstance(name, str)
            or sanitize_profile_name(name) != name
            or name in ("global", "search")
            for name in raw
        ):
            raise ValueError("The retired-profile index is invalid")
        self.retired = set(raw)

    @staticmethod
    def manifest(directory):
        try:
            raw = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if (
            not isinstance(raw, dict)
            or set(raw) != {"version", "identity", "isolated"}
            or type(raw["version"]) is not int
            or raw["version"] != 1
            or type(raw["isolated"]) is not bool
            or not isinstance(raw["identity"], str)
            or len(raw["identity"]) != 32
            or any(c not in "0123456789abcdef" for c in raw["identity"])
        ):
            raise ValueError("The profile storage manifest is invalid")
        return raw

    def retired_stub(self, name, directory):
        return (
            name in self.retired
            and directory.exists()
            and not (directory / MANIFEST).exists()
        )

    def active(self, name, directory):
        return directory.is_dir() and not self.retired_stub(name, directory)

    def prepare(self, name, directory, *, new=False):
        if self.retired_stub(name, directory):
            if not new:
                raise ValueError(
                    "This profile was renamed or archived. Choose an existing profile."
                )
            # Preserve even unexpected files; never delete late-write remnants.
            trash = self.config.config_dir / "deleted_profiles"
            trash.mkdir(exist_ok=True)
            directory.rename(trash / f"{name}-late-{uuid4().hex}")
        directory.mkdir(parents=True, exist_ok=True)
        if new:
            metadata = {"version": 1, "identity": uuid4().hex, "isolated": True}
            save_json(directory / MANIFEST, metadata)
        else:
            metadata = self.manifest(directory)
        suffix = metadata["identity"] if metadata and metadata["isolated"] else ""
        return directory / "storage" / suffix, directory / "cache" / suffix

    def retire(self, name, directory):
        # If the rename itself fails, this marker keeps the original directory
        # visible. Moving/copying the complete archive back also restores it.
        if not self.manifest(directory):
            save_json(
                directory / MANIFEST,
                {"version": 1, "identity": uuid4().hex, "isolated": False},
            )
        if name not in self.retired:
            retired = self.retired | {name}
            save_json(self.index, sorted(retired))
            self.retired = retired
