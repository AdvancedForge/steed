"""Secure DNS (DNS over HTTPS): Settings → General → Secure DNS.

The engine call is browser/webengine.set_dns_mode (PyQt6 doesn't bind Qt's
setDnsMode, so it calls Qt's library directly). Off leaves DNS to the system,
as before. The mode applies at once, to every profile, including incognito.
"""

# Well-known public resolvers; Other… takes any https:// template.
PROVIDERS = {
    "https://cloudflare-dns.com/dns-query": "Cloudflare",
    "https://dns.google/dns-query": "Google",
    "https://dns.quad9.net/dns-query": "Quad9",
    "https://dns.mullvad.net/dns-query": "Mullvad",
}
MODES = ("off", "automatic", "strict")


def check(mode, server):
    """Raise ValueError for a mode or server Settings must not save."""
    if mode not in MODES:
        raise ValueError("Choose how Steed looks up website addresses.")
    if mode != "off":
        if not server.startswith("https://") or " " in server or len(server) > 512:
            raise ValueError("Enter a secure DNS server address starting https://")


def apply(config):
    """Set the engine's DNS mode from Settings; False if it was refused."""
    from browser import webengine

    mode = config.get("secure_dns", "off")
    server = str(config.get("secure_dns_server", "") or "").strip()
    try:
        check(mode, server)
    except ValueError:
        mode = "off"
    if mode == "off":
        return webengine.set_dns_mode("system")
    return webengine.set_dns_mode(
        "fallback" if mode == "automatic" else "secure", [server]
    )
