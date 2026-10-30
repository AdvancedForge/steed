"""Bounded action inputs and receipts without control values or caller scripts."""

from dataclasses import dataclass


def action_arguments(
    operation, *, text=None, index=None, steps=None, to_target_id=None
):
    if operation == "drag":
        if (
            text is not None
            or index is not None
            or steps is not None
            or not isinstance(to_target_id, str)
            or not 1 <= len(to_target_id) <= 128
        ):
            raise ValueError("Drag requires to_target_id only")
        return {"operation": operation, "to_target_id": to_target_id}
    if to_target_id is not None:
        raise ValueError("Only drag accepts to_target_id")
    if operation == "adjust":
        if (
            text is not None
            or index is not None
            or type(steps) is not int
            or not 1 <= abs(steps) <= 100
        ):
            raise ValueError("Adjust requires steps from -100 to 100, not 0")
        return {"operation": operation, "steps": steps}
    if steps is not None:
        raise ValueError("Only adjust accepts steps")
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
    if operation in ("activate", "hover") and text is None and index is None:
        return {"operation": operation}
    raise ValueError(
        "Use enter, select, activate, hover, drag or adjust with the appropriate arguments"
    )


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
    if condition in ("settled", "changed") and text is None:
        return
    raise ValueError(
        "Wait for ready, settled or changed, or provide 1 to 256 characters for text"
    )


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
    value_text: str | None = None
    # Scrolled container: scroll width, scroll height, client width, client height.
    container_size: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class FileUploadResult:
    session_id: str
    action_id: str
    target_id: str
    upload_id: str
    status: str
    file_name: str
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class PageWaitResult:
    session_id: str
    condition: str
    status: str
    url: str
    revision: int
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormSubmissionResult:
    session_id: str
    action_id: str
    target_id: str
    status: str
    revision: int
    url: str
    request_authorized: bool
    site_effects: str = "unknown"
