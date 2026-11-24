"""Shared contract for external runtimes; providers never own browser authority."""

from PyQt6.QtCore import QObject, pyqtSignal


def count(value):
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def usage(input_tokens=0, output_tokens=0, cached_tokens=0, cost_usd=None):
    """One turn's usage as Steed records it, or None when nothing was reported.
    Cost is only ever what the runtime itself reported, never an estimate."""
    report = {
        "input_tokens": count(input_tokens),
        "output_tokens": count(output_tokens),
        "cached_tokens": count(cached_tokens),
    }
    if isinstance(cost_usd, (int, float)) and 0 <= cost_usd < 10_000:
        report["cost_usd"] = round(float(cost_usd), 6)
    if not any(report.values()):
        return None
    return report


class AgentProvider(QObject):
    request_resolved = pyqtSignal(str)
    requested = pyqtSignal(str, object)
    ready = pyqtSignal()
    accepted = pyqtSignal(str)
    message = pyqtSignal(str, str, bool)
    activity = pyqtSignal(str)
    model_reported = pyqtSignal(object)
    # Tokens (and cost, when the runtime says) one turn used: see usage().
    usage_reported = pyqtSignal(object)
    turn_done = pyqtSignal()
    failed = pyqtSignal(str)

    def start(self, discovery, binding_file):
        raise NotImplementedError

    def send(self, message_id, text):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError
