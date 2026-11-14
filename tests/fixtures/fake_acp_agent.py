"""Synthetic ACP agent: JSON-RPC over stdio, without a model or account."""

import json
import os
import socket
import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
session = "sess_fake"
waiting = {}  # our request id -> what to do with the answer
next_id = 100


def send(value):
    print(json.dumps(value), flush=True)


def update(update):
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session, "update": update},
        }
    )


def chunk(text):
    update(
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        }
    )


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        assert message["params"]["protocolVersion"] == 1
        caps = message["params"]["clientCapabilities"]
        assert caps["fs"] == {"readTextFile": False, "writeTextFile": False}
        assert caps["terminal"] is False
        version = 2 if mode == "version" else 1
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": version,
                    "agentCapabilities": {},
                    "authMethods": [],
                },
            }
        )
    elif method == "session/new":
        params = message["params"]
        assert os.path.isabs(params["cwd"])
        (server,) = params["mcpServers"]
        assert server["name"] == "steed" and "--task-binding-file" in server["args"]
        if mode == "auth":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32000, "message": "Authentication required"},
                }
            )
            continue
        # As the MCP server it was given would: connect, bound to the task.
        binding_path = server["args"][server["args"].index("--task-binding-file") + 1]
        with open(binding_path) as handle:
            binding = json.load(handle)
        bridge = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bridge.connect(binding["socket"])
        pipe = bridge.makefile("rwb", buffering=0)
        pipe.write(
            json.dumps(
                {"token": binding["token"], "task_binding": binding["task_binding"]}
            ).encode()
            + b"\n"
        )
        assert json.loads(pipe.readline()) == {"connected": True}
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"sessionId": session}})
    elif method == "session/prompt":
        prompt = message["params"]["prompt"]
        text = prompt[-1]["text"]
        first = len(prompt) > 1 and prompt[0]["text"].startswith("[Steed]")
        turn = message["id"]
        if text == "ask":
            # Asks permission; replies after the answer.
            next_id += 1
            waiting[next_id] = turn
            send(
                {
                    "jsonrpc": "2.0",
                    "id": next_id,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": session,
                        "toolCall": {
                            "toolCallId": "call_1",
                            "title": "Run tests",
                            "kind": "execute",
                            "rawInput": {"command": "pytest"},
                        },
                        "options": [
                            {
                                "optionId": "yes",
                                "name": "Allow once",
                                "kind": "allow_once",
                            },
                            {
                                "optionId": "always",
                                "name": "Always",
                                "kind": "allow_always",
                            },
                            {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                        ],
                    },
                }
            )
            continue
        if text == "files":
            # Asks for something Steed does not offer.
            next_id += 1
            waiting[next_id] = ("files", turn)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": next_id,
                    "method": "fs/read_text_file",
                    "params": {"sessionId": session, "path": "/etc/hostname"},
                }
            )
            continue
        update(
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "hmm"},
            }
        )
        chunk("Synthetic ")
        chunk(f"reply: {text}" + (" (with Steed's note)" if first else ""))
        update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "c2",
                "title": "Look",
                "kind": "read",
                "status": "completed",
            }
        )
        chunk("Done.")
        send({"jsonrpc": "2.0", "id": turn, "result": {"stopReason": "end_turn"}})
    elif method == "session/cancel":
        pass
    elif "id" in message and message["id"] in waiting:
        found = waiting.pop(message["id"])
        if isinstance(found, tuple):
            error = message.get("error", {})
            chunk(f"fs refused: {error.get('code')}")
            send(
                {"jsonrpc": "2.0", "id": found[1], "result": {"stopReason": "end_turn"}}
            )
        else:
            outcome = message["result"]["outcome"]
            chunk(f"Permission: {outcome.get('optionId', outcome['outcome'])}")
            send({"jsonrpc": "2.0", "id": found, "result": {"stopReason": "end_turn"}})
