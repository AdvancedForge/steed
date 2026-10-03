"""Synthetic App Server child: exercises actual IPC without models or accounts."""

import json
import socket
import sys
import time

mode = sys.argv[1]
bridge = None
thread = "synthetic-thread"
turn_counter = 0


def emit(value):
    print(json.dumps(value), flush=True)


def event(method, params):
    emit({"method": method, "params": params})


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method, params = request["method"], request.get("params", {})
    result = {}
    if method == "initialize":
        result = {"userAgent": "codex_cli_rs/0.160.0"}
    elif method == "account/read":
        result = {"account": None, "requiresOpenaiAuth": mode == "signin"}
    elif method == "config/read":
        result = {"config": {"mcp_servers": {"synthetic-other": {"enabled": True}}}}
    elif method == "thread/start":
        assert "environments" not in params
        assert "runtimeWorkspaceRoots" not in params
        assert "permissions" not in params
        assert "approvalPolicy" not in params
        servers = params["config"]["mcp_servers"]
        assert "synthetic-other" not in servers
        server_name = next(k for k in servers if k.startswith("steed_"))
        args = servers[server_name]["args"]
        binding_path = args[args.index("--task-binding-file") + 1]
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
        result = {
            "thread": {"id": thread},
            "approvalPolicy": "never",
            "runtimeWorkspaceRoots": [],
            "activePermissionProfile": {"id": "steed_chat"},
        }
        if mode == "unsafe-profile":
            result["activePermissionProfile"] = {"id": ":danger-full-access"}
    elif method == "mcpServerStatus/list":
        assert params["threadId"] == thread
        assert params["serverName"] == server_name
        result = {
            "data": [
                {"name": server_name},
            ],
            "nextCursor": None,
        }
        if mode == "extra-server":
            result["data"].append({"name": "synthetic-other"})
    elif method == "mcpServer/tool/call":
        pipe.write(
            json.dumps({"id": 1, "tool": "steed_task_status", "arguments": {}}).encode()
            + b"\n"
        )
        response = json.loads(pipe.readline())
        assert "result" in response
        result = {"structuredContent": {"result": response["result"]}, "content": []}
        if mode == "wrong-task":
            result["structuredContent"]["result"]["task_id"] = "not-the-task"
    elif method == "turn/start":
        if mode == "hang":
            continue
        turn_counter += 1
        turn = "turn-" + str(turn_counter)
        assert params["threadId"] == thread
        text = params["input"][0]["text"]
        result = {"turn": {"id": turn}}
        if mode == "disconnect":
            raise SystemExit(0)
        if mode == "malformed":
            print("not-json", flush=True)
            continue
        if mode == "oversized":
            print("x" * (1024 * 1024 + 1), flush=True)
            continue
        if mode == "host-request":
            emit(
                {
                    "id": "host",
                    "method": "item/commandExecution/requestApproval",
                    "params": {"command": "synthetic-private-command"},
                }
            )
            continue
        scope = {"threadId": thread, "turnId": turn}
        if mode == "wrong-thread":
            scope["threadId"] = "other-thread"
        elif mode == "wrong-turn":
            scope["turnId"] = "other-turn"
        answer = "Synthetic reply: " + text
        if mode == "long":
            answer = "🌍" * 2500
        event("turn/started", {"threadId": thread, "turn": {"id": turn}})
        if mode == "early-events":
            event(
                "item/agentMessage/delta",
                {**scope, "itemId": "reply", "delta": answer[:8]},
            )
        if mode == "clock":
            emit(
                {
                    "id": "early-clock",
                    "method": "currentTime/read",
                    "params": {"threadId": thread},
                }
            )
            assert (
                type(json.loads(sys.stdin.readline())["result"]["currentTimeAt"]) is int
            )
        emit({"id": request["id"], "result": result})
        if mode in ("approval", "question", "question-current", "permission"):
            params = {**scope, "itemId": "native-item", "startedAtMs": 1}
            method = "item/commandExecution/requestApproval"
            params["command"] = "synthetic-native-command"
            if mode in ("question", "question-current"):
                method = (
                    "item/tool/requestUserInput"
                    if mode == "question-current"
                    else "tool/requestUserInput"
                )
                params["isBlocking"] = True
                params["questions"] = [
                    {
                        "id": "choice",
                        "header": "Question",
                        "question": "Choose synthetic output",
                        "options": [
                            {"label": "Plain", "description": "Synthetic plain text"}
                        ],
                    }
                ]
            elif mode == "permission":
                method = "item/permissions/requestApproval"
                params["permissions"] = {"network": {"enabled": True}}
                params["cwd"] = "/tmp"
            emit({"id": "native-request", "method": method, "params": params})
            response = json.loads(sys.stdin.readline())
            assert response["id"] == "native-request" and "result" in response
            if mode in ("question", "question-current"):
                assert response["result"]["answers"]["choice"]["answers"] == ["Plain"]
            elif mode == "permission":
                assert response["result"]["permissions"] == {}
            else:
                assert response["result"]["decision"] == "acceptForSession"
        if mode == "child-events":
            child = {"threadId": "synthetic-child", "turnId": "child-turn"}
            event(
                "turn/started",
                {"threadId": child["threadId"], "turn": {"id": child["turnId"]}},
            )
            event(
                "item/started",
                {**child, "item": {"type": "reasoning", "id": "child-item"}},
            )
            event(
                "item/agentMessage/delta",
                {**child, "itemId": "child-item", "delta": "CHILD_PRIVATE"},
            )
            event(
                "item/completed",
                {
                    **child,
                    "item": {
                        "type": "agentMessage",
                        "id": "child-item",
                        "text": "CHILD_PRIVATE",
                    },
                },
            )
            event(
                "turn/completed",
                {
                    "threadId": child["threadId"],
                    "turn": {"id": child["turnId"], "status": "completed"},
                },
            )
        if mode in ("clock", "unsupported-rpc"):
            emit(
                {
                    "id": "optional-request",
                    "method": "currentTime/read"
                    if mode == "clock"
                    else "synthetic/optional",
                    "params": {
                        "threadId": "synthetic-clock-child"
                        if mode == "clock"
                        else thread
                    },
                }
            )
            response = json.loads(sys.stdin.readline())
            assert response["id"] == "optional-request"
            if mode == "clock":
                assert type(response["result"]["currentTimeAt"]) is int
            else:
                assert response["error"]["code"] == -32601
        if mode == "tool-work":
            event(
                "item/started",
                {**scope, "item": {"type": "commandExecution", "id": "tool"}},
            )
            time.sleep(0.35)
        if mode != "early-events":
            event(
                "item/agentMessage/delta",
                {**scope, "itemId": "reply", "delta": answer[:8]},
            )
        if mode == "slow":
            time.sleep(0.35)
        event(
            "item/agentMessage/delta", {**scope, "itemId": "reply", "delta": answer[8:]}
        )
        event(
            "item/completed",
            {**scope, "item": {"type": "agentMessage", "id": "reply", "text": answer}},
        )
        event(
            "turn/completed",
            {"threadId": thread, "turn": {"id": turn, "status": "completed"}},
        )
        if mode == "clock":
            emit(
                {
                    "id": "finished-clock",
                    "method": "currentTime/read",
                    "params": {"threadId": thread},
                }
            )
            assert (
                type(json.loads(sys.stdin.readline())["result"]["currentTimeAt"]) is int
            )
        continue
    emit({"id": request["id"], "result": result})
