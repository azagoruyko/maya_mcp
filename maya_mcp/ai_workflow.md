# Maya MCP AI workflow

Treat Maya as a structured scene, and keep inspection and edits scoped to the user's task.

## Inspecting a scene

1. Call `scene_context` first for the current file, selection, roots, units, references, and node counts.
2. Use `scene_naming` for naming patterns, `scene_hierarchy` for relevant DAG branches, and
   `scene_connections` for exact DG relationships. Treat inferred names and families as hypotheses.
3. Use `viewport_capture` when spatial or visual context matters.
4. For rigs, trace controls through constraints and utility nodes to driven joints or transforms.
   Inspect deformation only when relevant. Avoid broad graph dumps and large geometry arrays.

## Editing a scene

1. Use Rig Builder to inspect the active module tree and find reusable modules before creating new ones.
   Create task-specific modules under a descriptive workflow module, with clear documentation and
   explicit inputs and outputs. Compose reusable child modules when useful.
2. Use RigSystem's high-level APIs from module run code when they cover the task. Discover API names,
   signatures, and examples through Rig Builder; do not guess APIs. Use direct `maya.cmds` only when
   no suitable RigSystem API exists.
3. Never guess ambiguous scene-specific inputs such as mesh edge loops, guide curves, or target nodes
   from names or a viewport image. Reuse an existing module's input attributes where possible. Otherwise,
   expose clear user-facing attributes in a Rig Builder module and document the expected values or
   selection. Leave the module unexecuted until required inputs are supplied by the user.
4. Before execution, inspect the module code, supplied inputs, and target scope. Explain the intended
   changes and execute only the modules needed for the task. Do not edit unrelated rig modules.
5. After execution, use Maya MCP read tools to verify the result and report changes and scene save state.
   Preserve generally useful modules in the workspace library for reuse.

`maya_command` is a focused low-level fallback, not the default for reusable rig workflows.
Rig Builder's module hierarchy is an execution graph, not the Maya DAG; parenting modules does not
parent Maya scene nodes.
