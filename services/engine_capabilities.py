"""Describe implemented task authority; engine version never grants authority."""

import re
from dataclasses import dataclass

from PyQt6.QtCore import qVersion
from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion

from services.task_policy import SUPPORTED_ACTIONS

# Chrome's stable Connection-Allowlist feature starts at 152. Eligibility is
# only a prerequisite: delivery, frame/lifetime semantics and live grants still
# require integration and acceptance in the embedded backend.
CONNECTION_POLICY_CHROMIUM_MAJOR = 152


@dataclass(frozen=True)
class EngineCapabilities:
    backend: str
    qt_version: str
    chromium_version: str
    supported_actions: tuple[str, ...]
    website_scripts: bool
    script_unavailable_reason: str
    native_policy_candidate: bool
    required_chromium_major: int
    live_tab_delegation: bool = True
    live_website_scripts: bool = True
    live_network_policy: str = "normal_browser_background_traffic"


def qt_engine_capabilities() -> EngineCapabilities:
    version = qWebEngineChromiumVersion()
    match = re.fullmatch(r"([0-9]+)\.[0-9]+\.[0-9]+\.[0-9]+", version)
    candidate = False
    if match is None:
        reason = "engine_version_unverified"
    elif int(match[1]) < CONNECTION_POLICY_CHROMIUM_MAJOR:
        reason = "engine_upgrade_required"
    else:
        candidate = True
        reason = "native_policy_not_integrated"
    return EngineCapabilities(
        "qt_webengine",
        qVersion(),
        version,
        tuple(sorted(SUPPORTED_ACTIONS)),
        False,
        reason,
        candidate,
        CONNECTION_POLICY_CHROMIUM_MAJOR,
    )
