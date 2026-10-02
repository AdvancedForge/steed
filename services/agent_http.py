"""Optional authenticated loopback HTTP transport; Qt remains in the browser."""

import asyncio
import json
import os
import re
import secrets
import socket
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from agent_mcp import create_server, read_browser_discovery
from services.agent_tools import REQUEST_LIMIT


def create_http_app(discovery, credentials, authority, *, idle_timeout=60):
    """Give every HTTP session its own browser connection and credential owner."""
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    epoch = read_browser_discovery(discovery)["token"]
    manager = StreamableHTTPSessionManager(
        create_server(discovery, expected_epoch=epoch),
        json_response=True,
        stateless=False,
        event_store=None,
        session_idle_timeout=idle_timeout,
        max_sessions=4,
        max_request_body_size=REQUEST_LIMIT,
        security_settings=TransportSecuritySettings(
            allowed_hosts=[authority], allowed_origins=[]
        ),
    )

    @asynccontextmanager
    async def lifespan(_):
        async with manager.run():
            yield

    class Endpoint:
        async def __call__(self, scope, receive, send):
            request = Request(scope)
            # Check every method before handing any input to the SDK. Reject
            # duplicate security headers and all website-origin access.
            headers = request.headers
            if len(headers.getlist("host")) != 1 or headers.get("host") != authority:
                response = Response(status_code=421)
            elif "origin" in headers:
                response = Response(status_code=403)
            elif len(headers.getlist("mcp-session-id")) > 1:
                response = Response(status_code=400)
            elif len(headers.getlist("authorization")) != 1:
                response = Response(
                    status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
            else:
                supplied = headers["authorization"]
                owner = next(
                    (
                        name
                        for name, token in credentials.items()
                        if secrets.compare_digest(
                            supplied.encode(), ("Bearer " + token).encode()
                        )
                    ),
                    None,
                )
                if owner is None:
                    response = Response(
                        status_code=401, headers={"WWW-Authenticate": "Bearer"}
                    )
                elif not gateway_available(discovery, epoch):
                    response = Response(status_code=503)
                else:
                    scope["user"] = AuthenticatedUser(
                        AccessToken(
                            token=credentials[owner], client_id=owner, scopes=[]
                        )
                    )
                    await manager.handle_request(scope, receive, send)
                    return
            await response(scope, receive, send)

    return Starlette(
        routes=[Route("/mcp", Endpoint(), methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )


def gateway_available(discovery, epoch):
    """Never reconnect an HTTP credential to a new browser access run."""
    try:
        return secrets.compare_digest(read_browser_discovery(discovery)["token"], epoch)
    except (OSError, ValueError, RuntimeError):
        return False


def publish_endpoint(path, payload):
    """Publish a complete private file without overwriting an existing server."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        metadata = path.lstat()
        return metadata.st_dev, metadata.st_ino
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def serve_http(discovery, port=0, endpoint_file=None, client_names=None):
    import sys

    import uvicorn

    discovery = Path(discovery)
    epoch = read_browser_discovery(discovery)["token"]
    names = client_names or ["default"]
    if (
        len(names) > 4
        or len(set(names)) != len(names)
        or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) for name in names)
    ):
        raise ValueError("Use up to four distinct HTTP client names")
    credentials = {name: secrets.token_urlsafe(32) for name in names}
    endpoint_file = (
        Path(endpoint_file) if endpoint_file else discovery.with_name("agent-http.json")
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    runner = None
    published = None
    try:
        listener.bind(("127.0.0.1", port))
        listener.listen(16)
        actual_port = listener.getsockname()[1]
        authority = f"127.0.0.1:{actual_port}"
        app = create_http_app(discovery, credentials, authority)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                access_log=False,
                proxy_headers=False,
                log_level="warning",
                timeout_graceful_shutdown=2,
                limit_concurrency=16,
            )
        )
        runner = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            if runner.done():
                await runner
                raise RuntimeError("HTTP server did not start")
            await asyncio.sleep(0.01)
        url = f"http://{authority}/mcp"
        published = publish_endpoint(
            endpoint_file,
            {
                "version": 1,
                "url": url,
                "tokens": credentials,
            },
        )
        print(
            f"Steed MCP HTTP: {url}; private credentials: {endpoint_file}",
            file=sys.stderr,
        )
        while not runner.done():
            if not gateway_available(discovery, epoch):
                server.should_exit = True
            await asyncio.sleep(0.25)
        await runner
    finally:
        try:
            if runner is not None:
                server.should_exit = True
                await runner
        finally:
            listener.close()
            if published is not None:
                try:
                    metadata = endpoint_file.lstat()
                    if (metadata.st_dev, metadata.st_ino) == published:
                        endpoint_file.unlink()
                except FileNotFoundError:
                    pass
