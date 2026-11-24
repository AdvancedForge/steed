"""Synthetic Claude Code child: the stream-json protocol without models or accounts."""

import json
import socket
import sys
import uuid

mode = sys.argv[1]
arguments = sys.argv[2:]
config = json.loads(arguments[arguments.index("--mcp-config") + 1])
server_name, server = next(iter(config["mcpServers"].items()))
assert arguments[arguments.index("--permission-prompt-tool") + 1] == "stdio"
assert arguments[arguments.index("--allowedTools") + 1] == f"mcp__{server_name}"
disallowed = arguments[arguments.index("--disallowedTools") + 1 :][:4]
assert disallowed == [
    f"mcp__{server_name}__steed_task_start",
    f"mcp__{server_name}__steed_task_resume",
    f"mcp__{server_name}__steed_conversation_reply",
    f"mcp__{server_name}__steed_conversation_wait",
], disallowed
if mode == "resume":
    # Continuing a saved session: as a copy, in its own folder.
    assert "--no-session-persistence" not in arguments
    resumed = arguments[arguments.index("--resume") + 1]
    assert "--fork-session" in arguments
elif mode == "continue":
    # Carrying on Steed's own kept chat: the same session, not a copy.
    assert "--no-session-persistence" not in arguments
    resumed = arguments[arguments.index("--resume") + 1]
    assert "--fork-session" not in arguments
elif mode == "keep":
    # A kept chat is saved: no flag that turns saving off.
    assert "--no-session-persistence" not in arguments
    assert "--resume" not in arguments
    resumed = None
else:
    assert "--no-session-persistence" in arguments
    resumed = None
# Claude Code's session: the same one when carried on, else a new id.
session_id = resumed if mode == "continue" else str(uuid.uuid4())
model = (
    arguments[arguments.index("--model") + 1] if "--model" in arguments else "default"
)
binding_path = server["args"][server["args"].index("--task-binding-file") + 1]
bridge = None
status_polls = 0
turns = 0
waiting = None


def emit(value):
    print(json.dumps(value), flush=True)


def stream(message_id, event, parent=None):
    emit(
        {
            "type": "stream_event",
            "event": event,
            "parent_tool_use_id": parent,
            "api_message_id": message_id,
        }
    )


def reply(text):
    global turns
    turns += 1
    message_id = f"msg_synthetic_{turns}"
    if turns == 1:
        emit(
            {
                "type": "system",
                "subtype": "init",
                "model": f"claude-{model}",
                "session_id": session_id,
            }
        )
    if mode == "subagent":
        stream(
            "msg_child",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            "toolu_1",
        )
        stream(
            "msg_child",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "CHILD"},
            },
            "toolu_1",
        )
    if mode == "no-partial":
        emit(
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "message": {
                    "id": message_id,
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
    else:
        stream(message_id, {"type": "message_start", "message": {"id": message_id}})
        stream(
            message_id,
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
        )
        stream(message_id, {"type": "content_block_stop", "index": 0})
        stream(
            message_id,
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        )
        for part in (text[: len(text) // 2], text[len(text) // 2 :]):
            stream(
                message_id,
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": part},
                },
            )
        stream(message_id, {"type": "content_block_stop", "index": 1})
        # The complete message repeats streamed text; it must not duplicate it.
        emit(
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "message": {
                    "id": message_id,
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
    if mode == "error-result":
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "Synthetic failure",
            }
        )
    else:
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": text,
                # Synthetic usage, as Claude Code reports it for each turn.
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 100,
                    "output_tokens": 20,
                },
                "total_cost_usd": 0.0123,
            }
        )


for line in sys.stdin:
    value = json.loads(line)
    kind = value.get("type")
    if kind == "control_request":
        request = value["request"]
        response = {}
        if request["subtype"] == "initialize":
            if mode == "startup-hang":
                continue
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
            response = {"commands": [], "models": [], "account": {}}
        elif request["subtype"] == "mcp_status":
            status_polls += 1
            status = "pending" if status_polls == 1 else "connected"
            if mode == "mcp-failed":
                status = "failed"
            response = {
                "mcpServers": [
                    {"name": "plugin:other", "status": "needs-auth"},
                    {"name": server_name, "status": status},
                ]
            }
        emit(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": value["request_id"],
                    "response": response,
                },
            }
        )
    elif kind == "control_response":
        response = value["response"]
        assert response["request_id"] == waiting[0]
        decision = response["response"]
        if waiting[1] == "AskUserQuestion":
            answer = decision.get("updatedInput", {}).get("answers", {})
            reply("Answer: " + json.dumps(answer, sort_keys=True))
        else:
            rules = decision.get("updatedPermissions") or []
            session = (
                "all commands"
                if rules
                and rules[0].get("rules") == [{"toolName": "Bash"}]
                and rules[0].get("destination") == "session"
                else "session"
                if rules
                else "once"
            )
            reply(f"Permission {decision['behavior']} ({session})")
        waiting = None
    elif kind == "user":
        text = value["message"]["content"]
        if mode == "disconnect":
            sys.exit(0)
        if mode == "permission":
            waiting = ("perm-1", "Bash")
            emit(
                {
                    "type": "control_request",
                    "request_id": "perm-1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "Bash",
                        "input": {"command": "echo synthetic"},
                        "permission_suggestions": [
                            {"type": "addRules", "destination": "session"}
                        ],
                    },
                }
            )
        elif mode == "question":
            waiting = ("ask-1", "AskUserQuestion")
            emit(
                {
                    "type": "control_request",
                    "request_id": "ask-1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "question": "Which colour?",
                                    "header": "C",
                                    "options": [
                                        {"label": "Blue", "description": "Calm"},
                                        {"label": "Red", "description": "Bold"},
                                    ],
                                    "multiSelect": False,
                                }
                            ]
                        },
                    },
                }
            )
        elif mode == "hook":
            emit(
                {
                    "type": "control_request",
                    "request_id": "hook-1",
                    "request": {"subtype": "hook_callback", "callback_id": "x"},
                }
            )
            reply("Synthetic reply: " + text)
        elif mode in ("resume", "continue"):
            import os

            reply(f"Resumed {resumed} in {os.getcwd()}: {text}")
        else:
            reply("Synthetic reply: " + text)
