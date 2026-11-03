"""ZMQ client used by the external MCP process."""

import threading
import uuid

import zmq

from .config import DEFAULT_TIMEOUT_MS, get_endpoint, get_token
from .errors import MayaRequestError, MayaUnavailableError


class MayaClient:
    """Send synchronous RPC requests to the bridge running in Maya."""

    def __init__(self, endpoint: str | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        """Initialize a client for one Maya bridge endpoint."""
        self.endpoint = endpoint or get_endpoint()
        self.timeout_ms = timeout_ms
        self._context = zmq.Context.instance()
        self._lock = threading.Lock()
        self._socket = self._create_socket()

    def _create_socket(self):
        """Create a configured REQ socket."""
        socket = self._context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        socket.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        socket.connect(self.endpoint)
        return socket

    def _reset_socket(self) -> None:
        """Restore REQ socket state after a timeout or transport error."""
        self._socket.close()
        self._socket = self._create_socket()

    def call(self, method: str, params: dict | None = None) -> dict:
        """Call one Maya bridge method and return its result."""
        request = {
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
            "token": get_token(),
        }

        with self._lock:
            try:
                self._socket.send_json(request)
                response = self._socket.recv_json()
            except zmq.Again as exc:
                self._reset_socket()
                raise MayaUnavailableError(
                    f"Maya bridge did not respond at {self.endpoint}. "
                    "Start it in Maya with: import maya_mcp; maya_mcp.start()"
                ) from exc
            except zmq.ZMQError as exc:
                self._reset_socket()
                raise MayaUnavailableError(f"Cannot communicate with Maya bridge: {exc}") from exc

        if response.get("id") != request["id"]:
            raise MayaRequestError("Maya bridge returned a response for another request")
        if not response.get("ok"):
            error = response.get("error", {})
            raise MayaRequestError(f"{error.get('type', 'MayaError')}: {error.get('message', 'Unknown error')}")

        return response["result"]

    def close(self) -> None:
        """Close the client socket without terminating the shared context."""
        with self._lock:
            self._socket.close()

