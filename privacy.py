"""Privacy on every page load: tracking parameters, HTTPS first, fingerprints.

- Tracking parameters (utm_*, fbclid, gclid… see link_cleaning) are removed
  from the address of every top-level page load.
- HTTPS first: http:// pages load as https://; when that fails, Steed loads
  http:// after all and remembers the site until it quits. Local addresses,
  IP addresses and explicit ports are left alone.
- Fingerprinting resistance (off by default; some sites may break): pages see
  common hardware values, canvas reads with tiny noise (stable per site for a
  session), and a generic graphics name.
"""

import ipaddress
import secrets
from urllib.parse import urlsplit, urlunsplit

from link_cleaning import clean_link

# Sites that failed over HTTPS this session: loaded over http:// instead.
http_only: set[str] = set()


def _local(host: str) -> bool:
    host = host.lower().strip("[]")
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".lan")):
        return True
    if "." not in host:
        return True  # Intranet names.
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def navigation(url: str, config) -> str:
    """Where a top-level page load should go instead (or url itself)."""
    if config is None:
        return url
    if config.get("strip_tracking", True):
        url = clean_link(url)
    if config.get("https_first", True):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (
            parts.scheme == "http"
            and host
            and parts.port is None
            and not _local(host)
            and host not in http_only
        ):
            url = urlunsplit(parts._replace(scheme="https"))
    return url


def fallback(failed_url: str, original: str) -> str | None:
    """The http:// address to load when an upgraded https:// load failed."""
    failed, asked = urlsplit(failed_url), urlsplit(original)
    if (
        failed.scheme == "https"
        and asked.scheme == "http"
        and failed.hostname
        and failed.hostname == asked.hostname
    ):
        http_only.add(failed.hostname)
        return original
    return None


def site_of(host: str) -> str:
    """The site a host belongs to, for "forget when closed" (www. dropped)."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


FINGERPRINT = r"""
(() => {
  const seed = __SEED__ + location.hostname;
  let hash = 2166136261;
  for (const c of seed) hash = Math.imul(hash ^ c.charCodeAt(0), 16777619) >>> 0;
  const define = (target, name, value) => {
    try { Object.defineProperty(target, name, { get: () => value, configurable: true }); } catch (error) {}
  };
  define(Navigator.prototype, "hardwareConcurrency", 4);
  define(Navigator.prototype, "deviceMemory", 8);
  define(Navigator.prototype, "plugins", []);
  define(Navigator.prototype, "languages", Object.freeze([navigator.language]));
  // Canvas reads: a few low bits change, the same way for a site this session.
  const noise = (data) => {
    let state = hash;
    for (let i = 0; i < data.length; i += 4) {
      state = Math.imul(state ^ (state >>> 15), 2246822507) >>> 0;
      if ((state & 31) === 0) data[i] ^= 1;
    }
  };
  const getImageData = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function (...args) {
    const image = getImageData.apply(this, args);
    noise(image.data);
    return image;
  };
  const marked = (canvas) => {
    try {
      const context = canvas.getContext("2d");
      if (!context || !canvas.width || !canvas.height) return;
      const image = getImageData.call(context, 0, 0, canvas.width, canvas.height);
      noise(image.data);
      context.putImageData(image, 0, 0);
    } catch (error) {}
  };
  for (const name of ["toDataURL", "toBlob"]) {
    const original = HTMLCanvasElement.prototype[name];
    HTMLCanvasElement.prototype[name] = function (...args) {
      marked(this);
      return original.apply(this, args);
    };
  }
  // Graphics: a generic name instead of the card and driver.
  for (const kind of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!kind) continue;
    const getParameter = kind.prototype.getParameter;
    kind.prototype.getParameter = function (name) {
      if (name === 0x9245) return "Steed";  // UNMASKED_VENDOR_WEBGL
      if (name === 0x9246) return "Steed WebGL";  // UNMASKED_RENDERER_WEBGL
      return getParameter.call(this, name);
    };
  }
})();
"""
# Fresh each time Steed starts: sites cannot follow you from one run to the next.
_SEED = secrets.token_hex(8)


def install_fingerprint_guard(profile, on: bool) -> None:
    """Add or remove the fingerprinting script in a profile's pages."""
    import webengine

    collection = profile.scripts()
    for script in collection.toList():
        if script.name() == "steed-fingerprint":
            collection.remove(script)
    if on:
        collection.insert(
            webengine.page_script(
                "steed-fingerprint",
                FINGERPRINT.replace("__SEED__", repr(_SEED)),
                world=webengine.PAGE_WORLD,
                when="start",
                frames=True,
            )
        )
