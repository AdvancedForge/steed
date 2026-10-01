"""Bounded action inputs and receipts without control values or caller scripts."""

from dataclasses import dataclass


def action_arguments(operation, *, text=None, index=None):
    if operation == "enter":
        if not isinstance(text, str) or index is not None:
            raise ValueError("Enter requires text only")
        try:
            units = len(text.encode("utf-16-le")) // 2
        except UnicodeError:
            raise ValueError("Text contains invalid Unicode") from None
        if units > 4096 or "\x00" in text:
            raise ValueError("Enter accepts at most 4096 UTF-16 units without NUL")
        return {"operation": operation, "text": text}
    if operation == "select":
        if text is not None or type(index) is not int or not 0 <= index < 1000:
            raise ValueError("Select requires an option index from 0 to 999")
        return {"operation": operation, "index": index}
    if operation == "activate" and text is None and index is None:
        return {"operation": operation}
    raise ValueError("Use enter, select, or activate with the appropriate arguments")


def scroll_arguments(dx, dy):
    if any(type(value) is not int or abs(value) > 10000 for value in (dx, dy)):
        raise ValueError("Scroll offsets must be integers between -10000 and 10000")
    return {"dx": dx, "dy": dy}


def wait_arguments(condition, text, timeout_ms):
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 10000:
        raise ValueError("Wait timeout must be from 1 to 10000 milliseconds")
    if condition == "ready" and text is None:
        return
    if condition == "text" and isinstance(text, str) and 0 < len(text) <= 256:
        return
    raise ValueError("Wait for ready, or provide 1 to 256 characters for text")


@dataclass(frozen=True)
class PageActionResult:
    session_id: str
    action_id: str
    operation: str
    status: str
    revision: int
    changed: bool
    input_semantics: str
    target_id: str | None = None
    frame_id: str | None = None
    checked: bool | None = None
    selected_index: int | None = None
    text_matches: bool | None = None
    scroll_position: tuple[int, int] | None = None


@dataclass(frozen=True)
class PageWaitResult:
    session_id: str
    condition: str
    status: str
    url: str
    revision: int
    limitations: tuple[str, ...] = ()
