# Maya MCP

Maya MCP connects an AI client to an open Autodesk Maya scene. It has two
parts: an external MCP server and a small bridge running inside Maya.

## Requirements

- Python 3.10 or newer for the external MCP server.
- Autodesk Maya with `pyzmq` installed in Maya's Python environment.
- Rig Builder and RigSystem available in Maya for reusable, module-based rig
  workflows. Scene inspection and direct MCP tools can be used without them.

## Setup

Install the MCP server in a regular Python environment outside Maya:

```powershell
cd D:\path\to\maya_mcp
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

In your MCP client, configure the server to run:

```text
D:\path\to\maya_mcp\.venv\Scripts\python.exe -m maya_mcp.mcp_server
```

In Maya's Python Script Editor, start the bridge:

```python
import sys

project_path = r"D:\path\to\maya_mcp"
if project_path not in sys.path:
    sys.path.insert(0, project_path)

import maya_mcp
maya_mcp.start()
```

The default local endpoint is `tcp://127.0.0.1:51608`. Set the same
`MAYA_MCP_ENDPOINT` in both environments to change it. Optionally, set the same
`MAYA_MCP_TOKEN` in both environments to protect the connection. Stop the
bridge from Maya with `maya_mcp.stop()`.

## Using Maya MCP

The server provides these tools:

- `scene_context` — current scene, selection, roots, units, references, and counts.
- `scene_naming` — naming patterns and likely control families.
- `scene_hierarchy` — a focused DAG hierarchy.
- `scene_connections` — connections between nodes and attributes.
- `viewport_capture` — an image of the Maya viewport.
- `maya_command` — a low-level fallback for native `maya.cmds` calls.

For rig edits, the intended workflow is to find or compose modules in Rig
Builder and use RigSystem's high-level APIs from those modules. Inspect and
verify the scene with Maya MCP before and after running a workflow. Do not guess
ambiguous inputs such as edge loops or guide curves; provide them as module
inputs and run the module after they are specified.

AI workflow guidance is included in the server instructions and available as
the `maya://ai-workflow` MCP resource.
