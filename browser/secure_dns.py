"""Secure DNS (DNS over HTTPS): Settings → General → Secure DNS.

Qt WebEngine has QWebEngineGlobalSettings::setDnsMode, but PyQt6 binds only
its DnsMode type, not the function. Steed calls the function in Qt's own
library, passing a DnsMode PyQt built (a class with a QStringList is passed by
reference on Linux). Off leaves DNS to the system, as before. The mode
applies at once, to every profile, including incognito.
"""

import ctypes
import os

# Well-known public resolvers; Other… takes any https:// template.
PROVIDERS = {
    "https://cloudflare-dns.com/dns-query": "Cloudflare",
    "https://dns.google/dns-query": "Google",
    "https://dns.quad9.net/dns-query": "Quad9",
    "https://dns.mullvad.net/dns-query": "Mullvad",
}
MODES = ("off", "automatic", "strict")
_SYMBOL = "_ZN24QWebEngineGlobalSettings10setDnsModeENS_7DnsModeE"


def check(mode, server):
    """Raise ValueError for a mode or server Settings must not save."""
    if mode not in MODES:
        raise ValueError("Choose how Steed looks up website addresses.")
    if mode != "off":
        if not server.startswith("https://") or " " in server or len(server) > 512:
            raise ValueError("Enter a secure DNS server address starting https://")


def _function():
    import PyQt6

    library = ctypes.CDLL(
        os.path.join(
            os.path.dirname(PyQt6.__file__), "Qt6", "lib", "libQt6WebEngineCore.so.6"
        )
    )
    function = getattr(library, _SYMBOL)
    function.restype = ctypes.c_bool
    function.argtypes = [ctypes.c_void_p]
    return function


def apply(config):
    """Set the engine's DNS mode from Settings; False if it was refused."""
    from PyQt6 import sip
    from PyQt6.QtWebEngineCore import QWebEngineGlobalSettings as Settings

    mode = config.get("secure_dns", "off")
    server = str(config.get("secure_dns_server", "") or "").strip()
    try:
        check(mode, server)
    except ValueError:
        mode = "off"
    choice = Settings.DnsMode()
    choice.secureMode = {
        "off": Settings.SecureDnsMode.SystemOnly,
        "automatic": Settings.SecureDnsMode.SecureWithFallback,
        "strict": Settings.SecureDnsMode.SecureOnly,
    }[mode]
    choice.serverTemplates = [] if mode == "off" else [server]
    try:
        return bool(_function()(sip.unwrapinstance(choice)))
    except (OSError, AttributeError):
        return False
