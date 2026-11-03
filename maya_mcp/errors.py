"""Errors shared by the bridge and MCP adapter."""


class MayaMcpError(RuntimeError):
    """Base error for Maya MCP operations."""


class MayaUnavailableError(MayaMcpError):
    """Raised when the Maya bridge cannot be reached."""


class MayaRequestError(MayaMcpError):
    """Raised when Maya rejects or fails a request."""

