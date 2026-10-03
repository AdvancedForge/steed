"""Loopback-only synthetic Responses streams for native Codex compatibility."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def functions(tools, namespace=None):
    for tool in tools:
        if tool.get("type") == "namespace":
            yield from functions(tool.get("tools", []), tool.get("name"))
        else:
            yield tool, namespace


class ResponsesFixture:
    def __init__(self):
        self.tools = []
        self.requests = 0
        self.status_called = False
        self.status_received = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length > 2 * 1024 * 1024:
                    self.send_error(413)
                    return
                request = json.loads(self.rfile.read(length))
                advertised = list(request.get("tools", []))
                for value in request.get("input", []):
                    if value.get("type") == "additional_tools":
                        advertised.extend(value.get("tools", []))
                fixture.tools.append(advertised)
                fixture.requests += 1
                number = fixture.requests
                text = "Native synthetic reply " + str(number)
                item = {
                    "type": "message",
                    "id": f"msg_{number}",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                    "phase": "final_answer",
                }
                response = {
                    "id": f"resp_{number}",
                    "object": "response",
                    "created_at": 0,
                    "status": "in_progress",
                    "output": [],
                }
                for value in request.get("input", []):
                    if (
                        value.get("type")
                        in ("function_call_output", "custom_tool_call_output")
                        and value.get("call_id") == "synthetic_status_call"
                    ):
                        fixture.status_received = "task_id" in str(
                            value.get("output", "")
                        )
                status_name, namespace = next(
                    (
                        (tool.get("name"), namespace)
                        for tool, namespace in functions(advertised)
                        if tool.get("name", "").endswith("steed_task_status")
                    ),
                    (None, None),
                )
                runtime = any(
                    tool.get("name") == "exec" and tool.get("type") == "custom"
                    for tool, _ in functions(advertised)
                )
                if (status_name or runtime) and not fixture.status_called:
                    fixture.status_called = True
                    call = {
                        "type": "function_call",
                        "id": "fc_synthetic",
                        "call_id": "synthetic_status_call",
                        "name": status_name,
                        "arguments": "{}",
                    }
                    if runtime:
                        call = {
                            "type": "custom_tool_call",
                            "id": "fc_synthetic",
                            "call_id": "synthetic_status_call",
                            "name": "exec",
                            "namespace": "functions",
                            "input": "const t = ALL_TOOLS.find(t => t.name.endsWith('steed_task_status')); if (!t) throw Error('Steed tool missing'); text(await tools[t.name]({}));",
                        }
                    elif namespace is not None:
                        call["namespace"] = namespace
                    response.update(
                        status="completed",
                        output=[call],
                        usage={
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                    )
                    events = [
                        {
                            "type": "response.created",
                            "response": {
                                **response,
                                "status": "in_progress",
                                "output": [],
                            },
                        },
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": {**call, "arguments": ""},
                        },
                        {
                            "type": "response.output_item.done",
                            "output_index": 0,
                            "item": call,
                        },
                        {"type": "response.completed", "response": response},
                    ]
                    self.send_events(events)
                    return
                events = [
                    {"type": "response.created", "response": dict(response)},
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": dict(item),
                    },
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text[:8],
                    },
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text[8:],
                    },
                ]
                item.update(
                    status="completed",
                    content=[{"type": "output_text", "text": text, "annotations": []}],
                )
                events.append(
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": item,
                    }
                )
                response.update(
                    status="completed",
                    output=[item],
                    usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                )
                events.append({"type": "response.completed", "response": response})
                self.send_events(events)

            def send_events(self, events):
                raw = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
