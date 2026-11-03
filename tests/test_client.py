"""Tests for the external ZMQ client."""

import threading

import pytest
import zmq

from maya_mcp.client import MayaClient
from maya_mcp.errors import MayaRequestError


def run_server(endpoint: str, response_factory) -> threading.Thread:
    """Run a one-request REP server for a client test."""
    ready = threading.Event()

    def serve() -> None:
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.setsockopt(zmq.LINGER, 0)
        socket.bind(endpoint)
        ready.set()
        request = socket.recv_json()
        socket.send_json(response_factory(request))
        socket.close()
        context.term()

    thread = threading.Thread(target=serve)
    thread.start()
    ready.wait(timeout=2)
    return thread


def test_client_returns_bridge_result() -> None:
    """The client unwraps a successful RPC response."""
    endpoint = "tcp://127.0.0.1:51618"
    thread = run_server(endpoint, lambda request: {
        "id": request["id"],
        "ok": True,
        "result": {"status": "ok"},
    })
    client = MayaClient(endpoint=endpoint, timeout_ms=1_000)

    assert client.call("ping") == {"status": "ok"}

    client.close()
    thread.join(timeout=2)


def test_client_raises_bridge_error() -> None:
    """The client exposes Maya-side errors to the MCP adapter."""
    endpoint = "tcp://127.0.0.1:51619"
    thread = run_server(endpoint, lambda request: {
        "id": request["id"],
        "ok": False,
        "error": {"type": "ValueError", "message": "bad request"},
    })
    client = MayaClient(endpoint=endpoint, timeout_ms=1_000)

    with pytest.raises(MayaRequestError, match="ValueError: bad request"):
        client.call("scene_query")

    client.close()
    thread.join(timeout=2)

