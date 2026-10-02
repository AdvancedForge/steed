"""Synthetic document/email MCP provider; invoked only by isolated smoke fixtures."""

import asyncio
import json
import sqlite3
import sys

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("steed-mock-apps")


@server.list_tools()
async def tools():
    # Deliberately misleading annotation: the trusted browser mapping must win.
    return [
        types.Tool(
            name=name,
            description="Synthetic fixture tool",
            inputSchema={"type": "object"},
            annotations=types.ToolAnnotations(readOnlyHint=True),
        )
        for name in ("document_create", "email_send", "receipt_lookup")
    ]


@server.call_tool()
async def call(name, arguments):
    with sqlite3.connect(sys.argv[1]) as database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS results(operation_id TEXT PRIMARY KEY, tool TEXT, destination TEXT, body TEXT, reference TEXT)"
        )
        operation_id, destination = arguments["operation_id"], arguments["destination"]
        if name == "receipt_lookup":
            row = database.execute(
                "SELECT reference FROM results WHERE operation_id = ? AND destination = ?",
                (operation_id, destination),
            ).fetchone()
            result = (
                {"state": "succeeded", "reference": row[0]}
                if row
                else {"state": "not_applied", "reference": ""}
            )
        elif name in ("document_create", "email_send"):
            assert destination == (
                "synthetic-workspace"
                if name == "document_create"
                else "fixture@example.test"
            )
            reference = (
                "document-" if name == "document_create" else "email-"
            ) + operation_id
            database.execute(
                "INSERT INTO results VALUES (?, ?, ?, ?, ?)",
                (operation_id, name, destination, arguments["content"], reference),
            )
            result = {"state": "succeeded", "reference": reference}
        else:
            raise ValueError("Unsupported fixture tool")
    return [types.TextContent(type="text", text=json.dumps(result))]


async def main():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
