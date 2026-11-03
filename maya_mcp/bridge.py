"""ZMQ request bridge loaded inside Autodesk Maya."""

import threading
import traceback

import zmq

from .config import get_endpoint, get_token


class MayaBridge:
    """Receive RPC requests and execute them on Maya's main thread."""

    def __init__(self, endpoint: str | None = None):
        """Create a stopped bridge for the requested endpoint."""
        self.endpoint = endpoint or get_endpoint()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._start_error = None
        self._thread = None
        self._service = None

    @property
    def running(self) -> bool:
        """Return whether the bridge worker is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        """Start listening for requests in a background thread."""
        if self.running:
            return self.endpoint

        from .maya_service import MayaSceneService

        self._service = MayaSceneService()
        self._stop_event.clear()
        self._ready_event.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, name="maya-mcp-zmq", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=2.0):
            raise RuntimeError("Timed out while starting the Maya MCP bridge")
        if self._start_error is not None:
            self._thread = None
            raise RuntimeError(f"Could not start Maya MCP bridge: {self._start_error}") from self._start_error

        return self.endpoint

    def stop(self) -> None:
        """Ask the worker thread to stop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        """Serve requests until the bridge is stopped."""
        context = None
        socket = None

        try:
            context = zmq.Context()
            socket = context.socket(zmq.REP)
            socket.setsockopt(zmq.LINGER, 0)
            socket.bind(self.endpoint)
            poller = zmq.Poller()
            poller.register(socket, zmq.POLLIN)
            self._ready_event.set()

            while not self._stop_event.is_set():
                if not dict(poller.poll(100)).get(socket):
                    continue

                request = socket.recv_json()
                socket.send_json(self._handle(request))
        except zmq.ZMQError as exc:
            self._start_error = exc
            self._ready_event.set()
        finally:
            if socket is not None:
                socket.close()
            if context is not None:
                context.term()

    def _handle(self, request: dict) -> dict:
        """Validate and execute one RPC request."""
        request_id = request.get("id")

        try:
            if request.get("token", "") != get_token():
                raise PermissionError("Invalid MAYA_MCP_TOKEN")

            method = request["method"]
            params = request.get("params", {})

            import maya.utils

            result = maya.utils.executeInMainThreadWithResult(self._service.dispatch, method, params)
            return {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            return {
                "id": request_id,
                "ok": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            }


_bridge = None


def start(endpoint: str | None = None) -> str:
    """Start the process-wide Maya MCP bridge."""
    global _bridge

    if _bridge is None:
        _bridge = MayaBridge(endpoint)

    address = _bridge.start()
    print(f"Maya MCP bridge listening at {address}")
    return address


def stop() -> None:
    """Stop the process-wide Maya MCP bridge."""
    global _bridge

    if _bridge is not None:
        _bridge.stop()
        _bridge = None
        print("Maya MCP bridge stopped")
