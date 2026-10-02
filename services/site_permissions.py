"""Exact website permissions, separate from agent authority and private state."""

import json
from pathlib import Path
from uuid import uuid4

from services.task_policy import origin_url
from utils import save_json

FEATURES = {
    "MediaAudioCapture": ("Microphone", "Use your microphone."),
    "MediaVideoCapture": ("Camera", "Use your camera."),
    "MediaAudioVideoCapture": (
        "Microphone and camera",
        "Use your microphone and camera.",
    ),
    "DesktopVideoCapture": ("Screen sharing", "Share a screen or window you select."),
    "DesktopAudioVideoCapture": (
        "Screen sharing with audio",
        "Share a screen or window you select, with audio.",
    ),
    "Notifications": ("Notifications", "Show desktop notifications."),
    "Geolocation": ("Location", "Read your location."),
    "MouseLock": ("Mouse lock", "Capture the mouse pointer until you release it."),
    "ClipboardReadWrite": ("Clipboard", "Read and write your clipboard."),
    "LocalFontsAccess": ("Local fonts", "Read information about installed fonts."),
}


class SitePermissions:
    """Save before granting; private choices never enter the persistent file."""

    def __init__(self, path):
        self.path = Path(path)
        self.private_rules = []

    def rules(self):
        try:
            if self.path.stat().st_size > 262144:
                raise ValueError("Website permission file is too large")
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return list(self.private_rules)
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("rules"), list)
            or len(data["rules"]) > 200
        ):
            raise ValueError("Invalid website permission file")
        for rule in data["rules"]:
            self._validate(rule)
        return data["rules"] + list(self.private_rules)

    @staticmethod
    def _validate(rule):
        if (
            not isinstance(rule, dict)
            or set(rule)
            != {"id", "origin", "feature", "mode", "key", "profile", "private"}
            or any(
                not isinstance(rule[field], str) for field in rule if field != "private"
            )
            or len(rule["id"]) != 32
            or rule["feature"] not in FEATURES
            or rule["mode"] not in ("global", "profile")
            or rule["private"] is not False
            or (rule["mode"] == "profile" and len(rule["key"]) != 32)
        ):
            raise ValueError("Invalid website permission rule")
        if origin_url(rule["origin"]) != rule["origin"]:
            raise ValueError("Invalid website permission origin")

    def allowed(self, origin, feature, *, key, private=False):
        origin = origin_url(origin)
        return any(
            rule["origin"] == origin
            and rule["feature"] == feature
            and rule["private"] == private
            and (rule["key"] == key or (rule["mode"] == "global" and not private))
            for rule in self.rules()
        )

    def remember(self, origin, feature, mode, *, key, profile, private=False):
        if mode not in ("global", "profile") or feature not in FEATURES or not key:
            raise ValueError("Invalid website permission choice")
        rule = {
            "id": uuid4().hex,
            "origin": origin_url(origin),
            "feature": feature,
            "mode": mode,
            "key": key,
            "profile": profile,
            "private": bool(private),
        }
        rules = self.rules()
        if len(rules) >= 200:
            raise ValueError("Remove an old website permission before saving another")

        # Replace the same exact permission choice instead of accumulating duplicates.
        def matching(item):
            return (
                item["origin"] == rule["origin"]
                and item["feature"] == feature
                and item["private"] == private
                and item["mode"] == mode
                and (item["key"] == key or (mode == "global" and not private))
            )

        rules = [item for item in rules if not matching(item)] + [rule]
        if private:
            self.private_rules = [item for item in rules if item["private"]]
        else:
            self._validate(rule)
            save_json(
                self.path,
                {"version": 1, "rules": [r for r in rules if not r["private"]]},
            )
        return rule

    def forget(self, rule_id):
        rules = [rule for rule in self.rules() if rule["id"] != rule_id]
        if any(rule["id"] == rule_id for rule in self.private_rules):
            self.private_rules = [r for r in rules if r["private"]]
        else:
            save_json(
                self.path,
                {"version": 1, "rules": [r for r in rules if not r["private"]]},
            )


def configure_permission_policy(profile):
    """Steed owns remembered choices; native persistence must not bypass its UI."""
    policy = getattr(type(profile), "PersistentPermissionsPolicy", None)
    if policy is not None:
        profile.setPersistentPermissionsPolicy(policy.AskEveryTime)
