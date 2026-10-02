"""Test native document connection policy in a separate Chromium test profile.

Ordinary and restricted pages share one synthetic profile. No Steed/user profile
is opened. This is an engine feasibility test, not a replacement browser backend.
"""

import argparse
import json
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from chromium_pipe import ChromiumPipe
from smoke_task_active_content import fixture


def check(binary="chromium"):
    requests, packets, connections = [], [], []
    stopping = threading.Event()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.1)

    def receive():
        while not stopping.is_set():
            try:
                packets.append(len(udp.recvfrom(2048)[0]))
            except TimeoutError:
                pass

    udp_thread = threading.Thread(target=receive, daemon=True)
    udp_thread.start()

    class TcpHandler(socketserver.BaseRequestHandler):
        def handle(self):
            connections.append(True)
            self.request.settimeout(0.1)
            try:
                self.request.recv(2048)
            except TimeoutError:
                pass

    tcp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TcpHandler)
    tcp.daemon_threads = True
    tcp_thread = threading.Thread(target=tcp.serve_forever, daemon=True)
    tcp_thread.start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Cookie", "")))
            body = (
                fixture(
                    origin, denied, udp.getsockname()[1], tcp_port=tcp.server_address[1]
                )
                if self.path in ("/ordinary", "/restricted", "/restricted-frame")
                else b"<title>Native fixture</title>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            if self.path == "/seed":
                self.send_header(
                    "Set-Cookie", "native_fixture=shared; Path=/; HttpOnly"
                )
            if self.path in ("/restricted", "/restricted-frame"):
                self.send_header("Content-Security-Policy", "worker-src 'none'")
                self.send_header(
                    "Connection-Allowlist",
                    f'("{origin}/*" "{frame_origin}/*");webrtc=block',
                )
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append(("POST " + self.path, self.headers.get("Cookie", "")))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    denied_http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{http.server_port}"
    frame_origin = f"http://localhost:{http.server_port}"
    denied = f"http://localhost:{denied_http.server_port}"
    http_thread = threading.Thread(target=http.serve_forever, daemon=True)
    http_thread.start()
    denied_thread = threading.Thread(target=denied_http.serve_forever, daemon=True)
    denied_thread.start()
    browser = None
    try:
        browser = ChromiumPipe(binary)
        version = browser.command("Browser.getVersion")["product"]
        _, user = browser.page()
        task_target, task = browser.page()
        report = {"engine": version, "steed_backend_changed": False, "cases": {}}

        def wait(predicate):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.01)
            raise TimeoutError("Native policy fixture timed out")

        def load(session, path, audit=False):
            if audit:
                # A reload of /ordinary must not reuse the previous result.
                browser.evaluate(session, "window.audit=undefined; true")
            browser.command("Page.navigate", {"url": origin + path}, session=session)
            wait(lambda: browser.evaluate(session, "location.pathname") == path)
            if audit:
                wait(lambda: browser.evaluate(session, "Boolean(window.audit?.done)"))

        def reset():
            requests.clear()
            packets.clear()
            connections.clear()

        def collect(name, session):
            wait(lambda: browser.evaluate(session, "Boolean(window.audit?.done)"))
            state = browser.evaluate(
                session,
                "JSON.stringify(audit, (key, value) => key === 'contexts' ? undefined : value)",
            )
            result = {
                "requests": sorted(
                    path
                    for path, _ in requests
                    if path
                    not in (
                        "/ordinary",
                        "/restricted",
                        "/restricted-frame",
                        "/favicon.ico",
                    )
                ),
                "stun_packets": len(packets),
                "turn_tcp_connections": len(connections),
                "javascript": json.loads(state),
            }
            report["cases"][name] = result
            browser.evaluate(
                session,
                "for (const c of audit.contexts) {"
                "if (c.terminate) c.terminate(); else if (c.port) c.port.close();"
                "else if (c.remove) c.remove(); else c.close(); } true",
            )
            return result

        def run(name, session, path):
            reset()
            load(session, path, audit=True)
            return collect(name, session)

        load(user, "/seed")
        browser.evaluate(user, "localStorage.setItem('native_fixture','shared'); true")
        ordinary = run("ordinary_before", user, "/ordinary")
        restricted = run("restricted", task, "/restricted")
        report["shared_storage"] = browser.evaluate(
            task, "localStorage.getItem('native_fixture') === 'shared'"
        )
        report["shared_cookie"] = any(
            path == "/restricted" and "native_fixture=shared" in cookie
            for path, cookie in requests
        )
        browser.evaluate(
            task, "localStorage.setItem('native_fixture','task-update'); true"
        )
        report["shared_live_storage"] = browser.evaluate(
            user, "localStorage.getItem('native_fixture') === 'task-update'"
        )
        browser.command(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            },
            session=task,
        )
        reset()
        browser.evaluate(
            task,
            "const f = document.createElement('iframe');"
            f"f.src = {json.dumps(frame_origin + '/restricted-frame')};"
            "document.body.append(f); true",
        )

        child_target_ids = set()

        def child_session():
            targets = browser.command("Target.getTargets")["targetInfos"]
            target_ids = {
                target["targetId"]
                for target in targets
                if target["type"] == "iframe" and target["url"].startswith(frame_origin)
            }
            child_target_ids.update(target_ids)
            return next(
                (
                    event["params"]["sessionId"]
                    for event in browser.events
                    if event.get("method") == "Target.attachedToTarget"
                    and event["params"]["targetInfo"]["type"] == "iframe"
                    and event["params"]["targetInfo"]["targetId"] in target_ids
                ),
                None,
            )

        wait(child_session)
        child = child_session()
        child_result = collect("restricted_cross_process_frame", child)
        report["cross_process_frame_verified"] = True

        # Native document policy should survive loss of the control connection.
        reset()
        expression = "window.detachedDone=false; setTimeout(async () => {"
        expression += f"try {{ await fetch({json.dumps(denied + '/denied-detached')}, {{mode:'no-cors'}}); }} catch (_) {{}}"
        expression += f"try {{ const r=new RTCPeerConnection({{iceTransportPolicy:'relay',iceServers:[{{urls:'turn:127.0.0.1:{tcp.server_address[1]}?transport=tcp',username:'synthetic',credential:'synthetic'}}]}});r.createDataChannel('x');await r.setLocalDescription(await r.createOffer());window.detachedRtc=r; }} catch (_) {{}}"
        expression += (
            "setTimeout(() => {window.detachedDone=true;}, 2500); }, 200); true"
        )
        browser.evaluate(task, expression)
        browser.command("Target.detachFromTarget", {"sessionId": task})
        time.sleep(3)
        task = browser.command(
            "Target.attachToTarget",
            {
                "targetId": task_target,
                "flatten": True,
            },
        )["sessionId"]
        wait(lambda: browser.evaluate(task, "Boolean(window.detachedDone)"))
        report["policy_survived_detach"] = (
            not any("denied-" in path for path, _ in requests)
            and not packets
            and not connections
        )

        concurrent = run("ordinary_during_restricted", user, "/ordinary")
        browser.command("Target.closeTarget", {"targetId": task_target})
        owned_targets = child_target_ids | {task_target}
        wait(
            lambda: (
                not any(
                    target["targetId"] in owned_targets
                    for target in browser.command("Target.getTargets")["targetInfos"]
                )
            )
        )
        report["revocation_disposed_task"] = True
        report["user_survived_revocation"] = browser.evaluate(
            user, "localStorage.getItem('native_fixture') === 'task-update'"
        )
        for control in (ordinary, concurrent):
            for route in (
                "/denied-get",
                "/denied-worker",
                "/denied-sharedWorker",
                "/denied-freshFrameWorker",
                "/denied-websocket",
            ):
                assert route in control["requests"], (
                    "Positive control unavailable",
                    route,
                )
            assert control["stun_packets"] > 0, "STUN positive control unavailable"
            assert control["turn_tcp_connections"] > 0, (
                "TURN/TCP positive control unavailable"
            )
        assert (
            report["shared_storage"]
            and report["shared_cookie"]
            and report["shared_live_storage"]
        ), "Profile sharing failed"
        for result in (restricted, child_result):
            assert result["javascript"]["started"], "Website-script fixture did not run"
            assert "/allowed-get" in result["requests"], "Allowed retrieval was blocked"
            assert "POST /allowed-post" in result["requests"], (
                "Connection policy unexpectedly blocked approved-origin POST"
            )
        report["tested_connection_policy_contained"] = (
            all(
                not any("denied-" in path for path in result["requests"])
                and result["stun_packets"] == 0
                and result["turn_tcp_connections"] == 0
                for result in (restricted, child_result)
            )
            and report["policy_survived_detach"]
            and report["revocation_disposed_task"]
            and report["user_survived_revocation"]
        )
        print(json.dumps(report, indent=2))
        if not report["tested_connection_policy_contained"]:
            raise RuntimeError("Native connection-policy gate failed")
        return report
    finally:
        if browser is not None:
            browser.close()
        http.shutdown()
        http.server_close()
        http_thread.join()
        denied_http.shutdown()
        denied_http.server_close()
        denied_thread.join()
        tcp.shutdown()
        tcp.server_close()
        tcp_thread.join()
        stopping.set()
        udp_thread.join()
        udp.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", default="chromium")
    check(parser.parse_args().chromium)
