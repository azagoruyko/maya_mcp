# Maya MCP

A compact MCP server for inspecting and editing a running Autodesk Maya scene.
The integration has two local processes:

```text
MCP client --stdio--> maya_mcp.mcp_server --ZMQ REQ/REP--> Maya bridge
```

The MCP adapter currently exposes six tools for the scene-project experiment:

- `scene_context`
- `scene_naming`
- `scene_hierarchy`
- `scene_connections`
- `viewport_capture`
- `maya_command`

The bridge still contains the earlier query, inspect, graph, patch, and verify
handlers, but they are intentionally not registered as MCP tools while the new
workflow is being tested.

Heavy geometry values such as mesh points, topology arrays, skin weights, and
blend-shape deltas are deliberately omitted.

`viewport_capture` returns a PNG from the active model panel or a specified
camera directly as MCP image content. It does not keep the temporary image on
disk. Width and height must be divisible by four and between 64 and 2048.

`scene_naming` provides a cheap first pass over prefixes, suffixes, likely
families, and NURBS control candidates. `scene_hierarchy` returns a compact
text DAG tree without shapes by default, so node identity metadata is not
repeated for every line. It shows transforms, joints, and IK handles by
default; pass `include_types: ["*"]` to include constraints and every DAG type.

`scene_connections` follows inputs, outputs, or both from focused nodes and
plugs. Connections are split by direction and keep exact plugs while placing
the node type directly beside each endpoint:

```yaml
inputs:
  - source (joint): M_spine_5_joint.worldMatrix
    destination (parentConstraint): L_arm_options_control_parentConstraint1.targetParentMatrix
```

Start with `depth: 1`; expand only when the next part of the graph is unclear.
Use `include_types` or `exclude_types` to focus the result without requesting a
flat dump of the entire DG.

`maya_command` calls one native `maya.cmds` command with JSON positional and
keyword arguments, or accepts a `commands` list for a batch. The complete call
or batch runs in one undo chunk and is rolled back when Maya raises an error.


## Install the external MCP server

Create a normal virtual environment outside Maya:

```powershell
cd D:\github\maya_mcp
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Configure the IDE to run:

```text
D:\github\maya_mcp\.venv\Scripts\python.exe
```

with arguments:

```text
-m maya_mcp.mcp_server
```

The MCP process communicates only through stdout/stdin. Diagnostic output must
therefore go to stderr or a log file.

## Start the bridge in Maya

The Maya Python environment needs access to `pyzmq`. Then run this in Maya's
Python Script Editor:

```python
import sys

project_path = r"D:\github\maya_mcp"
if project_path not in sys.path:
    sys.path.insert(0, project_path)

import maya_mcp
maya_mcp.start()
```

The default endpoint is `tcp://127.0.0.1:51608`. Override it in both processes
with `MAYA_MCP_ENDPOINT`. An optional matching `MAYA_MCP_TOKEN` can protect the
local endpoint.

Stop the bridge with:

```python
maya_mcp.stop()
```

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

These tests cover the external transport and Maya-independent helpers. The Maya
scene operations require an integration test in a running Maya process.
