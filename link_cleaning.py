"""Remove tracking parameters from a link before you share it."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parameters that only identify a campaign, click or sharer, never the page.
TRACKING = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "twclid",
        "ttclid",
        "li_fat_id",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "mkt_tok",
        "oly_anon_id",
        "oly_enc_id",
        "vero_id",
        "rb_clickid",
        "s_cid",
        "wickedid",
        "ns_mchannel",
        "ns_source",
        "ns_campaign",
        "ref_src",
        "ref_url",
        "spm",
    }
)
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "__hs", "ga_")


def is_tracking(name: str) -> bool:
    name = name.lower()
    return name in TRACKING or name.startswith(TRACKING_PREFIXES)


def clean_link(url: str) -> str:
    """The link without tracking parameters; other parameters keep their order."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.query:
        return url
    kept = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not is_tracking(name)
    ]
    return urlunsplit(parts._replace(query=urlencode(kept, doseq=True)))
