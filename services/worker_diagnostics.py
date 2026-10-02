"""Cached trusted sandbox self-check; never imports generated source or user data."""

import threading
from concurrent.futures import Future

from PyQt6.QtCore import QObject, QTimer

from services.command_broker import require_owner_thread
from services.local_worker import MAX_RPC, MAX_WORKERS, WALL_SECONDS, run_worker
from services.worker_bundle import BundleError, environment, require_installed

PROBE_SOURCE = b"""
import errno
import os
import resource
import socket
from pathlib import Path

assert os.environ.get('HOME') == '/work'
assert not Path('/home').exists() and not Path('/root').exists()
assert not Path('/run/user').exists()
status = Path('/proc/self/status').read_text()
assert 'Seccomp:\\t2' in status and 'NoNewPrivs:\\t1' in status
assert 'CapEff:\\t0000000000000000' in status
for kind, value in [
    (resource.RLIMIT_AS, 512 * 1024 * 1024), (resource.RLIMIT_CPU, 10),
    (resource.RLIMIT_FSIZE, 8 * 1024 * 1024), (resource.RLIMIT_NOFILE, 64),
    (resource.RLIMIT_NPROC, 0), (resource.RLIMIT_CORE, 0),
]:
    assert resource.getrlimit(kind) == (value, value)
try:
    socket.socket()
except OSError as error:
    assert error.errno == errno.EPERM
else:
    raise AssertionError('socket policy unavailable')
try:
    Path('/package/check.py').write_bytes(b'changed')
except OSError:
    pass
else:
    raise AssertionError('package is writable')
Path('/work/check').write_bytes(b'isolated scratch')
assert Path('/tmp/check').read_bytes() == b'isolated scratch'
"""

DOCUMENT_PROBE = (
    PROBE_SOURCE
    + b"""
from io import BytesIO
from reportlab.pdfgen import canvas
from openpyxl import Workbook, load_workbook
from PIL import Image
from defusedxml.ElementTree import fromstring
pdf = BytesIO()
page = canvas.Canvas(pdf)
page.drawString(72, 720, 'Steed sandbox document check')
page.save()
assert pdf.getvalue().startswith(b'%PDF-')
sheet = Workbook()
sheet.active['A1'] = 'Steed'
xlsx = BytesIO()
sheet.save(xlsx)
assert load_workbook(BytesIO(xlsx.getvalue())).active['A1'].value == 'Steed'
png = BytesIO()
Image.new('RGB', (8, 8), 'blue').save(png, format='PNG')
assert Image.open(BytesIO(png.getvalue())).size == (8, 8)
assert fromstring('<safe/>').tag == 'safe'
try:
    Path('/dependencies/PIL/__init__.py').write_bytes(b'changed')
except OSError:
    pass
else:
    raise AssertionError('dependencies are writable')
"""
)


def diagnostic_report(available, reason=""):
    """Report support/limits, never issue execution or app authority."""
    return {
        "available": available,
        "reason": reason,
        "runtime": "python_standard_library",
        "execution_approval": "exact_package",
        "direct_network": False,
        "subprocesses": False,
        "requirements": [
            "linux_user_namespaces",
            "bubblewrap",
            "libseccomp",
            "system_python",
        ],
        "limits": {
            "wall_seconds": WALL_SECONDS,
            "cpu_seconds": 10,
            "address_space_bytes": 512 * 1024 * 1024,
            "scratch_bytes": 32 * 1024 * 1024,
            "scratch_file_bytes": 8 * 1024 * 1024,
            "file_descriptors": 64,
            "rpc_calls": MAX_RPC,
            "concurrent_workers": MAX_WORKERS,
            "workers_per_task": 1,
            "runs_per_task": 50,
        },
    }


class WorkerDiagnostics(QObject):
    """One bounded background probe per browser run, with a trusted UI retry."""

    def __init__(self, parent):
        super().__init__(parent)
        self.future = None
        self.worker_thread = None
        self.cancelled = threading.Event()
        self._result = Future()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)

    def check(self):
        require_owner_thread(self)
        if self.future is None:
            self.future = Future()
            self.worker_thread = threading.Thread(target=self.run, daemon=True)
            self.timer.start()
            try:
                self.worker_thread.start()
            except Exception:
                self.timer.stop()
                self.future.set_result(diagnostic_report(False, "sandbox_unavailable"))
        return self.future

    def run(self):
        try:

            def reject_rpc(message):
                raise RuntimeError(
                    "The trusted setup check must not request task access"
                )

            outcome = run_worker(
                {"check.py": PROBE_SOURCE},
                "check.py",
                self.cancelled,
                reject_rpc,
                timeout=3,
            )
            result = diagnostic_report(
                outcome.state == "succeeded",
                "" if outcome.state == "succeeded" else "sandbox_unavailable",
            )
            standard = environment()
            result["environments"] = [
                {
                    **standard.metadata(),
                    "available": result["available"],
                    "reason": result["reason"],
                }
            ]
            documents = None
            try:
                documents = environment("documents")
                require_installed(documents)
                if not result["available"]:
                    raise BundleError("sandbox_unavailable")
                outcome = run_worker(
                    {"check.py": DOCUMENT_PROBE},
                    "check.py",
                    self.cancelled,
                    reject_rpc,
                    timeout=10,
                    environment=documents,
                )
                result["environments"].append(
                    {
                        **documents.metadata(),
                        "available": outcome.state == "succeeded",
                        "reason": ""
                        if outcome.state == "succeeded"
                        else "worker_bundle_check_failed",
                    }
                )
            except BundleError as error:
                result["environments"].append(
                    {
                        "runtime": "documents",
                        **(documents.metadata() if documents is not None else {}),
                        "available": False,
                        "reason": str(error),
                    }
                )
            except Exception:
                result["environments"].append(
                    {
                        "runtime": "documents",
                        "available": False,
                        "reason": "worker_bundle_check_failed",
                    }
                )
        except Exception:
            result = diagnostic_report(False, "sandbox_unavailable")
        self._result.set_result(result)

    def poll(self):
        require_owner_thread(self)
        if self._result.done():
            self.timer.stop()
            if self.future is not None and not self.future.done():
                self.future.set_result(self._result.result())

    def close(self):
        require_owner_thread(self)
        self.cancelled.set()
        self.timer.stop()
        if self.worker_thread is not None and self.worker_thread.ident is not None:
            self.worker_thread.join(timeout=2)
        if self.future is not None and not self.future.done():
            self.future.set_result(diagnostic_report(False, "setup_check_ended"))
