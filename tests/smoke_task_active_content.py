"""Audit script-enabled task networking against loopback-only positive controls.

This deliberately overrides the unsupported setting in synthetic task pages. It
reports gaps; a successful audit is NOT acceptance of website-script authority.
No real profile, account, external server, or debug listener is used.
"""

import json
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import qVersion
from PyQt6.QtWebEngineCore import QWebEngineSettings, qWebEngineChromiumVersion

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_task_profiles import TaskProfileTests  # noqa: E402


def fixture(origin, denied, udp_port, *, tcp_port=None, csp=False):
    """Retain contexts until inspection so worker/ICE requests can finish."""
    configuration = json.dumps(
        {"origin": origin, "denied": denied, "udpPort": udp_port, "tcpPort": tcp_port}
    )
    # Capability probe only: a fixture-controlled policy is not an enforcement
    # mechanism for arbitrary task documents or their child contexts.
    policy = (
        '<meta http-equiv="Content-Security-Policy" '
        f"content=\"worker-src 'none'; connect-src {origin}; webrtc 'block'\">"
        if csp
        else ""
    )
    return f"""<!doctype html><title>Active content audit</title>
    <link rel="icon" href="data:,">{policy}<body><script>
    const config = {configuration};
    window.audit = {{started: true, done: false, contexts: []}};
    const work = [];
    for (const [name, url, options] of [
        ['allowedGet', config.origin + '/allowed-get', {{}}],
        ['deniedGet', config.denied + '/denied-get', {{mode: 'no-cors'}}],
        ['allowedPost', config.origin + '/allowed-post', {{method: 'POST', body: 'synthetic'}}]
    ]) work.push(fetch(url, options).then(
        () => audit[name] = 'resolved', () => audit[name] = 'rejected'));
    for (const variant of [false, true, 'freshFrame']) {{
        const shared = variant !== false;
        const name = variant === 'freshFrame' ? 'freshFrameWorker' :
            (shared ? 'sharedWorker' : 'worker');
        let Realm = window;
        if (variant === 'freshFrame') {{
            const frame = document.createElement('iframe');
            document.body.append(frame);
            Realm = frame.contentWindow;
            audit.contexts.push(frame);
        }}
        const source = `const run = async port => {{
            try {{ await fetch(${{JSON.stringify(config.denied + '/denied-' + name)}},
                {{mode:'no-cors'}}); port.postMessage('resolved'); }}
            catch (_) {{ port.postMessage('rejected'); }}
        }}; ${{shared ? 'onconnect = event => run(event.ports[0]);' : 'run(self);'}}`;
        const url = URL.createObjectURL(new Blob([source], {{type:'text/javascript'}}));
        try {{
            const worker = shared ? new Realm.SharedWorker(url) : new Realm.Worker(url);
            const port = shared ? worker.port : worker;
            audit.contexts.push(worker);
            if (shared) port.start();
            work.push(new Promise(resolve => {{
                port.onmessage = event => {{ audit[name] = event.data; resolve(); }};
                worker.onerror = () => {{ audit[name] = 'error'; resolve(); }};
            }}));
        }} catch (_) {{ audit[name] = 'unavailable'; }}
    }}
    try {{
        const ws = new WebSocket(config.denied.replace('http:', 'ws:') + '/denied-websocket');
        audit.contexts.push(ws);
        work.push(new Promise(resolve => {{
            ws.onopen = () => {{ audit.websocket = 'opened'; ws.close(); resolve(); }};
            ws.onerror = () => {{ audit.websocket = 'error'; resolve(); }};
        }}));
    }} catch (_) {{ audit.websocket = 'unavailable'; }}
    try {{
        const rtc = new RTCPeerConnection({{iceServers:[
            {{urls: 'stun:127.0.0.1:' + config.udpPort}}
        ]}});
        audit.contexts.push(rtc);
        rtc.createDataChannel('synthetic');
        work.push(rtc.createOffer().then(offer => rtc.setLocalDescription(offer)).then(
            () => audit.rtc = 'gathering', () => audit.rtc = 'error'));
    }} catch (_) {{ audit.rtc = 'unavailable'; }}
    if (config.tcpPort) {{
        try {{
            const rtc = new RTCPeerConnection({{iceTransportPolicy:'relay', iceServers:[
                {{urls:'turn:127.0.0.1:' + config.tcpPort + '?transport=tcp',
                  username:'synthetic', credential:'synthetic'}}
            ]}});
            audit.contexts.push(rtc);
            rtc.createDataChannel('synthetic-tcp');
            work.push(rtc.createOffer().then(offer => rtc.setLocalDescription(offer)).then(
                () => audit.rtcTcp = 'gathering', () => audit.rtcTcp = 'error'));
        }} catch (_) {{ audit.rtcTcp = 'unavailable'; }}
    }}
    Promise.race([Promise.all(work), new Promise(resolve => setTimeout(resolve, 2500))])
        .then(() => setTimeout(() => audit.done = true, 500));
    </script>""".encode()


def audit(*, prepare_task=None, report_label="baseline"):
    case = TaskProfileTests(methodName="runTest")
    TaskProfileTests.setUpClass()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.1)
    packets = []
    connections = []
    stopping = threading.Event()

    def receive():
        while not stopping.is_set():
            try:
                packet, _ = udp.recvfrom(2048)
                packets.append(len(packet))
            except TimeoutError:
                pass

    listener = threading.Thread(target=receive, daemon=True)
    listener.start()

    class TcpHandler(socketserver.BaseRequestHandler):
        def handle(self):
            # Accepting a connection is already a network effect, even when a
            # packet-loss rule subsequently prevents TURN payload delivery.
            connections.append(True)
            self.request.settimeout(0.1)
            try:
                self.request.recv(2048)
            except TimeoutError:
                pass

    tcp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TcpHandler)
    tcp.daemon_threads = True
    tcp_listener = threading.Thread(target=tcp.serve_forever, daemon=True)
    tcp_listener.start()
    initialized = False
    try:
        case.setUp()
        initialized = True
        denied = f"http://localhost:{case.server.server_port}"
        type(case).page_fixtures["/audit"] = fixture(
            case.origin, denied, udp.getsockname()[1], tcp_port=tcp.server_address[1]
        )
        report = {
            "qt": qVersion(),
            "chromium": qWebEngineChromiumVersion(),
            "mode": report_label,
            "website_scripts_supported": False,
            "cases": {},
        }

        def evaluate(page, expression):
            values = []
            page.runJavaScript(expression, values.append)
            case.wait_for(lambda: bool(values))
            return values[0]

        def run(label, page, load, scripts):
            case.requests.clear()
            packets.clear()
            connections.clear()
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled, scripts
            )
            load()
            if scripts:
                case.wait_for(lambda: evaluate(page, "Boolean(window.audit?.done)"))
            else:
                # Give any unexpected background activity an observation window.
                deadline = time.monotonic() + 0.5
                case.wait_for(lambda: time.monotonic() >= deadline)
            state = evaluate(
                page,
                "window.audit ? JSON.stringify(audit, (key, value) => "
                "key === 'contexts' ? undefined : value) : null",
            )
            result = {
                "requests": sorted(
                    path
                    for path, _ in case.requests
                    if path not in ("/audit", "/favicon.ico")
                ),
                "stun_packets": len(packets),
                "turn_tcp_connections": len(connections),
                "javascript": json.loads(state) if state else None,
            }
            report["cases"][label] = result
            # Close active contexts before the next case's counters are reset.
            evaluate(
                page,
                "for (const context of window.audit?.contexts || []) {"
                "if (context.terminate) context.terminate();"
                "else if (context.port) context.port.close();"
                "else if (context.remove) context.remove();"
                "else context.close(); } true",
            )

        user = case.page()
        run("ordinary_user", user, lambda: case.load(user, "/audit"), True)
        for profile in ("private", "existing:work"):
            task = case.command(
                "task.create", objective="Synthetic active content audit"
            )
            grant = case.request(task.task_id, profile=profile)
            case.service.approve_grant(task.task_id, grant.grant_id)
            opened = case.command("session.open", task_id=task.task_id, profile=profile)
            page = case.service.backend._sessions[opened.session_id].page
            if prepare_task is not None:
                prepare_task(case, opened.session_id)

            def load(task_id=task.task_id, session_id=opened.session_id):
                case.navigate(task_id, session_id, "/audit")

            run(profile + "_scriptless", page, load, False)
            # Synthetic experiment ONLY. There is no scripts action in TaskScope.
            run(profile + "_forced_scripts", page, load, True)
            if profile == "existing:work":
                type(case).page_fixtures["/audit"] = fixture(
                    case.origin,
                    denied,
                    udp.getsockname()[1],
                    tcp_port=tcp.server_address[1],
                    csp=True,
                )
                run(profile + "_csp_probe", page, load, True)
                type(case).page_fixtures["/audit"] = fixture(
                    case.origin,
                    denied,
                    udp.getsockname()[1],
                    tcp_port=tcp.server_address[1],
                )
                run(
                    "ordinary_user_during_task",
                    user,
                    lambda: case.load(user, "/audit"),
                    True,
                )
            case.command("task.finish", task_id=task.task_id)

        for label in ("ordinary_user", "ordinary_user_during_task"):
            control = report["cases"][label]
            for route in (
                "/allowed-get",
                "/denied-get",
                "POST /allowed-post",
                "/denied-worker",
                "/denied-sharedWorker",
                "/denied-freshFrameWorker",
                "/denied-websocket",
            ):
                case.assertIn(
                    route, control["requests"], "Positive control did not reach fixture"
                )
            case.assertGreater(control["stun_packets"], 0, "STUN control unavailable")
            case.assertGreater(
                control["turn_tcp_connections"], 0, "TURN/TCP control unavailable"
            )
        for label, result in report["cases"].items():
            if label.endswith("_scriptless"):
                case.assertEqual(result["requests"], [])
                case.assertEqual(result["stun_packets"], 0)
                case.assertEqual(result["turn_tcp_connections"], 0)
                case.assertIsNone(result["javascript"])
        # If this engine understands worker-src, no blob/shared worker should
        # reach the forbidden HTTP fixture. RTC evidence remains diagnostic.
        csp_result = report["cases"]["existing:work_csp_probe"]
        case.assertNotIn("/denied-sharedWorker", csp_result["requests"])
        case.assertNotIn("/denied-worker", csp_result["requests"])
        case.assertNotIn("/denied-freshFrameWorker", csp_result["requests"])
        report["tested_routes_contained"] = all(
            not any(
                "denied-" in path or path.startswith("POST ")
                for path in result["requests"]
            )
            and result["stun_packets"] == 0
            and result["turn_tcp_connections"] == 0
            for label, result in report["cases"].items()
            if label.endswith("_forced_scripts")
        )
        print(json.dumps(report, indent=2))
        return report
    finally:
        if initialized:
            case.tearDown()
        stopping.set()
        listener.join()
        udp.close()
        tcp.shutdown()
        tcp.server_close()
        tcp_listener.join()
        TaskProfileTests.tearDownClass()


if __name__ == "__main__":
    audit()
