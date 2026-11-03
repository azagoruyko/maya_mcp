"""Shared configuration for the Maya bridge and MCP adapter."""

import os


DEFAULT_ENDPOINT = "tcp://127.0.0.1:51608"
DEFAULT_TIMEOUT_MS = 10_000


def get_endpoint() -> str:
    """Return the configured local Maya bridge endpoint."""
    return os.environ.get("MAYA_MCP_ENDPOINT", DEFAULT_ENDPOINT)


def get_token() -> str:
    """Return the optional shared bridge token."""
    return os.environ.get("MAYA_MCP_TOKEN", "")

