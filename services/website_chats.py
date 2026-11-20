"""Chatbot websites as assistants: a chat site opened in the sidebar, signed
in as you. It reaches Steed's tools only through the relay you review, or
through a pairing link if the site supports connectors."""

from PyQt6.QtCore import QUrl

WEB_CHATS = {
    "ChatGPT": "https://chatgpt.com/",
    "Claude": "https://claude.ai/",
    "Gemini": "https://gemini.google.com/",
}


def clean_website_chat(value):
    """A chatbot website from settings: {"name", "url"} (https only)."""
    if not isinstance(value, dict):
        raise ValueError("Describe a chat website with a name and an address")
    name, url = value.get("name"), value.get("url")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError("Give the website a name of up to 40 characters")
    parsed = QUrl(url) if isinstance(url, str) else QUrl()
    if (
        not parsed.isValid()
        or parsed.scheme() != "https"
        or not parsed.host()
        or parsed.userInfo()
        or len(url) > 512
    ):
        raise ValueError("Give the chat website's https address")
    return {"name": name.strip(), "url": url}
