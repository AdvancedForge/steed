"""Installed Codex handshake against synthetic Steed MCP; no external AI requests."""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.responses_server import ResponsesFixture  # noqa: E402
from PyQt6.QtCore import QProcessEnvironment  # noqa: E402
from qt_test_support import application  # noqa: E402

from services.agent_gateway import AgentGateway  # noqa: E402
from services.codex_provider import CodexProvider  # noqa: E402
from services.task_service import TaskService  # noqa: E402
from utils import save_json  # noqa: E402


class Probe(CodexProvider):
    def _account(self, _):
        # The dedicated test home has no credentials. Only this probe skips login.
        self._request("config/read", {"includeLayers": False}, self._config)


def run(model="gpt-6.1-sol"):
    if shutil.which("codex") is None:
        raise RuntimeError("This compatibility check requires an installed Codex CLI")
    app = application()
    responses = ResponsesFixture()
    with tempfile.TemporaryDirectory(prefix="steed-codex-smoke-") as directory:
        root = Path(directory)
        native_home, chat_home = root / "codex", root / "chat"
        native_home.mkdir()
        chat_home.mkdir()
        # Native settings and a harmless inherited MCP marker must be preserved.
        # Model prewarming, if any,
        # points at the synthetic loopback fixture instead of an external API.
        marker = root / "unrelated-server-started"
        (native_home / "config.toml").write_text(
            'sandbox_mode = "workspace-write"\n'
            'model_provider = "synthetic"\n'
            "[model_providers.synthetic]\n"
            'name = "Synthetic offline provider"\n'
            f"base_url = {json.dumps(responses.url)}\n"
            'wire_api = "responses"\n'
            "requires_openai_auth = false\n"
            "[mcp_servers.unrelated]\n"
            f'command = "{sys.executable}"\n'
            f"args = [\"-c\", \"open({str(marker)!r}, 'w').write('started')\"]\n"
        )
        environment = QProcessEnvironment.systemEnvironment()
        # This is Codex's supported per-process config directory, never a shell
        # variable or a change to the user's existing authentication/configuration.
        environment.insert("CODEX_HOME", str(native_home))
        # Do not inherit the enclosing coding-agent session's runtime identity or
        # CI/tool-host hints. This is a separate synthetic app-server instance.
        for key in (
            "CODEX_CI",
            "CODEX_SESSION_ID",
            "CODEX_THREAD_ID",
            "CODEX_VERSION",
            "CODEX_PERMISSION_PROFILE",
            "CODEX_SANDBOX_NETWORK_DISABLED",
        ):
            environment.remove(key)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            environment.remove(key)
        service = TaskService(root / "tasks.sqlite")
        gateway = AgentGateway(service, root)
        driver = Probe(environment=environment, model=model)
        outcome = []
        replies, turns, accepted = [], [], []
        driver.message.connect(
            lambda item, text, final: replies.append((item, text, final))
        )
        driver.turn_done.connect(lambda: turns.append(True))
        driver.accepted.connect(accepted.append)
        task = service._create("Synthetic native Codex handshake")
        binding = chat_home / "binding.json"
        save_json(
            binding,
            {
                "version": 1,
                "socket": gateway.socket_path,
                "token": gateway.token,
                "task_binding": gateway.bind_task(task.task_id),
            },
        )
        driver.ready.connect(
            lambda: outcome.append(driver.bound_task_id == task.task_id)
        )
        driver.failed.connect(lambda _: outcome.append(False))
        try:
            driver.start(gateway.discovery, binding)
            deadline = time.monotonic() + 55
            while not outcome and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert outcome == [True], (
                "Installed Codex could not establish the scoped chat"
            )
            assert marker.exists(), "Native inherited MCP configuration was suppressed"
            assert service.store.grants(task.task_id) == []
            assert service.messages.read(task.task_id)["messages"] == []
            assert os.stat(binding).st_mode & 0o777 == 0o600
            for number in (1, 2):
                driver.send(f"synthetic_message_{number}", f"Synthetic prompt {number}")
                deadline = time.monotonic() + 20
                while len(turns) < number and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                assert len(turns) == number, "Native synthetic reply did not complete"
                assert accepted[-1] == f"synthetic_message_{number}"
                assert replies[-1][2] is True and replies[-1][1].startswith(
                    "Native synthetic reply"
                )
            # Inspect only tool names from this synthetic thread, never accounts.
            flat_names = []
            for tools in responses.tools:
                for tool in tools:
                    flat_names.append(tool.get("name", tool.get("type", "")))
                    flat_names.extend(
                        child.get("name", "") for child in tool.get("tools", [])
                    )
            if model == "gpt-5.4":
                assert any("steed_task_status" in name for name in flat_names)
            else:
                assert "exec" in flat_names, "Newer Codex code runtime was unavailable"
            assert responses.status_called and responses.status_received, (
                "Native model fixture did not complete its Steed MCP tool round trip"
            )
            driver.stop()
            deadline = time.monotonic() + 5
            while gateway.connections and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert not gateway.connections, (
                "Native adapter did not disconnect after stop"
            )
            assert service.store.get(task.task_id).status == "cancelled"
            print(
                "Codex handshake, native streamed follow-ups, task binding, native capability preservation and cleanup passed; no external model calls."
            )
        finally:
            driver.stop()
            gateway.close()
            service.close()
            responses.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "gpt-6.1-sol")
