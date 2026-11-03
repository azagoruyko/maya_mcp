"""External stdio MCP adapter for the Maya ZMQ bridge."""

import base64
from importlib.resources import files
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from .client import MayaClient


AI_WORKFLOW = files("maya_mcp").joinpath("ai_workflow.md").read_text(encoding="utf-8")


mcp = FastMCP(
    "Maya",
    instructions=AI_WORKFLOW,
)
_client = MayaClient()


@mcp.resource(
    "maya://ai-workflow",
    name="Maya AI workflow",
    description="Guidance for AI scene inspection and Rig Builder/RigSystem workflows.",
)
def maya_ai_workflow() -> str:
    """Provide the AI workflow guidance as an MCP resource."""
    return AI_WORKFLOW


@mcp.tool
def scene_context(
    count_types: list[str] | None = None,
    root_limit: int = 100,
    include_plugins: bool = False,
) -> dict[str, Any]:
    """Get scene metadata, selection, roots, units, time, and node counts."""
    params = {"root_limit": root_limit, "include_plugins": include_plugins}
    if count_types is not None:
        params["count_types"] = count_types
    return _client.call("scene_context", params)


@mcp.tool
def scene_naming(
    max_examples: int = 3,
    prefix_limit: int = 15,
    suffix_limit: int = 20,
    family_limit: int = 20,
) -> dict[str, Any]:
    """Summarize common prefixes, suffixes, families, and NURBS control candidates."""
    return _client.call("scene_naming", {
        "max_examples": max_examples,
        "prefix_limit": prefix_limit,
        "suffix_limit": suffix_limit,
        "family_limit": family_limit,
    })


@mcp.tool
def scene_hierarchy(
    roots: list[str] | None = None,
    depth: int = 3,
    include_shapes: bool = False,
    include_types: list[str] | None = None,
    max_nodes: int = 200,
) -> dict[str, Any]:
    """Read a compact text DAG hierarchy from roots or the current selection."""
    return _client.call("scene_hierarchy", {
        "roots": roots or [],
        "depth": depth,
        "include_shapes": include_shapes,
        "include_types": include_types,
        "max_nodes": max_nodes,
    })


@mcp.tool
def scene_connections(
    targets: list[str] | None = None,
    direction: Literal["inputs", "outputs", "both"] = "both",
    depth: int = 1,
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
    max_connections: int = 200,
) -> dict[str, Any]:
    """Follow compact input/output plug connections from nodes or plugs."""
    return _client.call("scene_connections", {
        "targets": targets or [],
        "direction": direction,
        "depth": depth,
        "include_types": include_types,
        "exclude_types": exclude_types,
        "max_connections": max_connections,
    })


@mcp.tool
def viewport_capture(
    camera: str | None = None,
    panel: str | None = None,
    width: int = 1280,
    height: int = 720,
    show_ornaments: bool = False,
    off_screen: bool = True,
) -> Image:
    """Capture the active Maya viewport or a specified camera and return it as a PNG image."""
    result = _client.call("viewport_capture", {
        "camera": camera,
        "panel": panel,
        "width": width,
        "height": height,
        "show_ornaments": show_ornaments,
        "off_screen": off_screen,
    })
    return Image(data=base64.b64decode(result["image_base64"]), format="png")


@mcp.tool
def maya_command(
    command: str | None = None,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    commands: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute one native maya.cmds command or a batch in a single undo chunk."""
    return _client.call("maya_command", {
        "command": command,
        "args": args or [],
        "kwargs": kwargs or {},
        "commands": commands,
    })


def main() -> None:
    """Run the external MCP adapter over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
