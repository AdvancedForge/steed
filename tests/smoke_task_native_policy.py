"""Loopback-only native policy prototype; never imported by the application.

Compare Fetch response continuation/fulfillment and WebRTC packet loss at zero
or one millisecond latency. These controls do not contain TURN/TCP connections.
The bounded ASCII fixtures do not establish production body/encoding semantics,
cross-process frame coverage, or policy lifetime after adapter loss.
"""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QUrl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smoke_task_active_content import audit  # noqa: E402

import services.task_network as network  # noqa: E402

PROTOTYPE_SCRIPT = """
window.__steedNativePolicy = {responses: 0, failures: []};
const conditions = await target.networkAgent().invoke_emulateNetworkConditions({
    offline: false, latency: PROTOTYPE_LATENCY,
    downloadThroughput: -1, uploadThroughput: -1,
    packetLoss: PROTOTYPE_PACKET_LOSS, packetQueueLength: 0, packetReordering: false
});
if (conditions.getError()) throw new Error('network_conditions_unavailable');
const agent = target.fetchAgent();
const multi = SDK.NetworkManager.MultitargetNetworkManager.instance();
// The SDK normally continues unhandled Fetch events. Replace that handler in
// this isolated frontend; adding another dispatcher races its continuation.
multi.requestIntercepted = async event => {
    try {
        const headers = [...(event.responseHeaders || []), {
            name: 'Content-Security-Policy', value: "worker-src 'none'"
        }];
        if (PROTOTYPE_ALLOWLIST) headers.push({
            name: 'Connection-Allowlist', value: '(response-origin);webrtc=block'
        });
        let response;
        if (event.responseStatusCode) {
            if (PROTOTYPE_FULFILL && event.responseStatusCode === 200) {
                const body = await agent.invoke_getResponseBody({requestId:event.requestId});
                if (body.getError()) throw new Error('response_body_unavailable');
                // This bounds processing after retrieval, NOT native buffering.
                if (body.body.length > 2000000) throw new Error('fixture_body_too_large');
                response = await agent.invoke_fulfillRequest({
                    requestId: event.requestId, responseCode: event.responseStatusCode,
                    responseHeaders: headers,
                    body: body.base64Encoded ? body.body :
                        btoa(unescape(encodeURIComponent(body.body)))
                });
            } else {
                response = await agent.invoke_continueResponse({
                    requestId: event.requestId, responseCode: event.responseStatusCode,
                    responseHeaders: headers
                });
            }
        } else {
            response = await agent.invoke_continueRequest({requestId:event.requestId});
        }
        if (response.getError()) throw new Error('response_policy_unavailable');
        window.__steedNativePolicy.responses++;
    } catch (error) {
        window.__steedNativePolicy.failures.push(String(error));
        await agent.invoke_failRequest({requestId:event.requestId, errorReason:'BlockedByClient'});
    }
};
const enabled = await agent.invoke_enable({patterns:[{
    urlPattern: '*', resourceType: 'Document', requestStage: 'Response'
}]});
if (enabled.getError()) throw new Error('response_interception_unavailable');
"""


def native_audit(
    *, delivery="fulfill", latency_ms=1, packet_loss=100, connection_allowlist=False
):
    if (
        delivery not in ("continue", "fulfill")
        or latency_ms not in (0, 1)
        or packet_loss not in (0, 100)
        or not isinstance(connection_allowlist, bool)
    ):
        raise ValueError("Unsupported prototype configuration")
    script = (
        PROTOTYPE_SCRIPT.replace("PROTOTYPE_LATENCY", str(latency_ms))
        .replace("PROTOTYPE_FULFILL", "true" if delivery == "fulfill" else "false")
        .replace("PROTOTYPE_PACKET_LOSS", str(packet_loss))
        .replace("PROTOTYPE_ALLOWLIST", "true" if connection_allowlist else "false")
    )
    prepare = network.PREPARE_SCRIPT.replace(
        "window.__steedTaskNetwork = true;",
        script + "\nwindow.__steedTaskNetwork = true;",
    )

    def prepare_task(case, session_id):
        session = case.service.backend._sessions[session_id]
        if session.network is None:
            # A never-loaded private page has no usable protocol target yet.
            session.page.network_ready = False
            session.page.setUrl(QUrl("about:blank"))
            session.network = network.TaskNetwork(session.page, case.service.backend)
        case.wait_for(session.network.ready.done)
        session.network.ready.result()

    with patch.object(network, "PREPARE_SCRIPT", prepare):
        return audit(
            prepare_task=prepare_task,
            report_label=(
                f"native-prototype: delivery={delivery}, latency_ms={latency_ms}, "
                f"packet_loss={packet_loss}, connection_allowlist={connection_allowlist}"
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delivery", choices=("continue", "fulfill"), default="fulfill"
    )
    parser.add_argument("--latency-ms", choices=(0, 1), type=int, default=1)
    parser.add_argument("--packet-loss", choices=(0, 100), type=int, default=100)
    parser.add_argument("--connection-allowlist", action="store_true")
    parser.add_argument(
        "--require-containment",
        action="store_true",
        help="Exit nonzero if any tested task networking route escapes containment.",
    )
    options = parser.parse_args()
    configuration = vars(options)
    require_containment = configuration.pop("require_containment")
    # Stable configuration summary; no response bodies or profile data are printed.
    print(json.dumps(configuration))
    report = native_audit(**configuration)
    if require_containment and not report["tested_routes_contained"]:
        print("Native networking gate failed: tested task traffic escaped containment.")
        raise SystemExit(1)
