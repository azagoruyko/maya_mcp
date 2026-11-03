"""MCP integration for Autodesk Maya."""

from .bridge import start, stop
from .config import DEFAULT_ENDPOINT

__all__ = ["DEFAULT_ENDPOINT", "start", "stop"]
__version__ = "0.1.0"
