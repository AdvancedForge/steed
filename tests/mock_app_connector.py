"""Trusted test-only mappings to a real MCP document/email fixture process."""

import asyncio
import json
import sys
from pathlib import Path

from services.connectors import ConnectorOutcome, ConnectorTool


def register_mock_apps(registry, state_path):
    async def invoke(tool, arguments):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("mock_app_server.py")), str(state_path)],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                response = await client.call_tool(tool, arguments)
                if response.isError:
                    raise RuntimeError("Synthetic provider rejected the call")
                data = json.loads(response.content[0].text)
                return ConnectorOutcome(**data)

    def execute(tool):
        def run(request, cancel):
            outcome = asyncio.run(
                asyncio.wait_for(
                    invoke(
                        tool,
                        {
                            "operation_id": request.operation_id,
                            "destination": request.destination,
                            "content": request.artifact.content,
                        },
                    ),
                    10,
                )
            )
            if request.request_id == "uncertain-email":
                raise TimeoutError(
                    "Simulated lost provider acknowledgement after commit"
                )
            return outcome

        return run

    def reconcile(probe, cancel):
        return asyncio.run(
            asyncio.wait_for(
                invoke(
                    "receipt_lookup",
                    {
                        "operation_id": probe.operation_id,
                        "destination": probe.destination,
                    },
                ),
                10,
            )
        )

    registry.register(
        ConnectorTool(
            "documents",
            "create",
            "write",
            "Create a synthetic document in the approved workspace",
            True,
            execute("document_create"),
            reconcile,
        )
    )
    registry.register(
        ConnectorTool(
            "email",
            "send",
            "send",
            "Send a synthetic email only to the approved recipient",
            True,
            execute("email_send"),
            reconcile,
        )
    )
