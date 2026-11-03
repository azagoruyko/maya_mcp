"""Maya scene operations executed inside the Maya process."""

import base64
from collections import Counter
import fnmatch
import os
import re
import tempfile


HEAVY_ATTRIBUTE_TYPES = {
    "mesh",
    "nurbsCurve",
    "nurbsSurface",
    "lattice",
    "pointArray",
    "vectorArray",
    "doubleArray",
    "Int32Array",
}

PATCH_OPERATIONS = {
    "create_node",
    "delete_node",
    "rename_node",
    "parent",
    "add_attr",
    "remove_attr",
    "set_attr",
    "reset_attr",
    "connect",
    "disconnect",
}


class MayaSceneService:
    """Expose compact, JSON-compatible Maya scene operations."""

    def __init__(self):
        """Load Maya commands and initialize the bridge revision."""
        import maya.cmds as cmds

        self.cmds = cmds
        self.revision = 0

    def dispatch(self, method: str, params: dict) -> dict:
        """Dispatch an RPC method from a fixed allow-list."""
        handlers = {
            "ping": self.ping,
            "scene_context": self.scene_context,
            "scene_naming": self.scene_naming,
            "scene_hierarchy": self.scene_hierarchy,
            "scene_connections": self.scene_connections,
            "scene_query": self.scene_query,
            "scene_inspect": self.scene_inspect,
            "scene_graph": self.scene_graph,
            "scene_patch": self.scene_patch,
            "scene_verify": self.scene_verify,
            "viewport_capture": self.viewport_capture,
            "maya_command": self.maya_command,
        }
        if method not in handlers:
            raise ValueError(f"Unknown Maya MCP method: {method}")

        return handlers[method](params)

    def ping(self, params: dict) -> dict:
        """Return bridge availability and Maya version."""
        result = {
            "status": "ok",
            "maya_version": self.cmds.about(version=True),
            "scene_revision": self.revision,
        }
        return result

    def scene_context(self, params: dict) -> dict:
        """Return a compact overview of the current Maya scene."""
        cmds = self.cmds
        scene_path = cmds.file(query=True, sceneName=True)
        references = []
        for path in cmds.file(query=True, reference=True) or []:
            references.append({
                "file": path,
                "namespace": cmds.file(path, query=True, namespace=True),
                "loaded": cmds.referenceQuery(path, isLoaded=True),
            })

        count_types = params.get("count_types", ["transform", "joint", "mesh", "camera", "animCurve"])
        counts = {
            "dag": len(cmds.ls(dagObjects=True, long=True) or []),
            "dg": len(cmds.ls(dependencyNodes=True) or []),
        }
        for node_type in count_types:
            counts[node_type] = len(cmds.ls(type=node_type) or [])

        roots = cmds.ls(assemblies=True, long=True) or []
        limit = min(params.get("root_limit", 100), 500)
        result = {
            "scene": {
                "file": scene_path,
                "name": scene_path.rsplit("/", 1)[-1] if scene_path else "untitled",
                "modified": cmds.file(query=True, modified=True),
                "revision": self.revision,
            },
            "maya": {
                "version": cmds.about(version=True),
                "api_version": cmds.about(apiVersion=True),
                "batch": cmds.about(batch=True),
            },
            "units": {
                "linear": cmds.currentUnit(query=True, linear=True),
                "angle": cmds.currentUnit(query=True, angle=True),
                "time": cmds.currentUnit(query=True, time=True),
                "up_axis": cmds.upAxis(query=True, axis=True),
            },
            "time": {
                "current": cmds.currentTime(query=True),
                "playback_min": cmds.playbackOptions(query=True, minTime=True),
                "playback_max": cmds.playbackOptions(query=True, maxTime=True),
            },
            "selection": [self._node_record(node) for node in cmds.ls(selection=True, long=True) or []],
            "dag_roots": [self._node_record(node) for node in roots[:limit]],
            "references": references,
            "namespaces": cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or [],
            "counts": counts,
            "complete": len(roots) <= limit,
        }
        if params.get("include_plugins", False):
            result["plugins"] = cmds.pluginInfo(query=True, listPlugins=True) or []

        return result

    def scene_naming(self, params: dict) -> dict:
        """Summarize naming patterns and likely control families in the scene."""
        cmds = self.cmds
        max_examples = min(params.get("max_examples", 3), 20)
        prefix_limit = min(params.get("prefix_limit", 15), 100)
        suffix_limit = min(params.get("suffix_limit", 20), 100)
        family_limit = min(params.get("family_limit", 20), 100)
        names = [node.rsplit("|", 1)[-1] for node in cmds.ls(dependencyNodes=True, long=True) or []]
        summary = self._naming_summary(
            names,
            max_examples=max_examples,
            prefix_limit=prefix_limit,
            suffix_limit=suffix_limit,
            family_limit=family_limit,
        )

        curve_shapes = cmds.ls(type="nurbsCurve", long=True) or []
        controls = []
        seen = set()
        for shape in curve_shapes:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parents and parents[0] not in seen:
                seen.add(parents[0])
                controls.append(self._node_record(parents[0]))

        control_names = [control["name"] for control in controls]
        control_summary = self._naming_summary(
            control_names,
            max_examples=max_examples,
            prefix_limit=prefix_limit,
            suffix_limit=suffix_limit,
            family_limit=family_limit,
        )
        summary["controls"] = {
            "count": len(controls),
            "examples": controls[:max_examples],
            "families": control_summary["families"],
        }
        summary["scene_revision"] = self.revision
        return summary

    def scene_hierarchy(self, params: dict) -> dict:
        """Return a compact text DAG hierarchy from roots or the current selection."""
        cmds = self.cmds
        roots = params.get("roots") or cmds.ls(selection=True, long=True) or cmds.ls(assemblies=True, long=True) or []
        depth = min(max(params.get("depth", 3), 0), 20)
        max_nodes = min(max(params.get("max_nodes", 200), 1), 2_000)
        include_shapes = params.get("include_shapes", False)
        include_types = params.get("include_types") or ["transform", "joint", "ikHandle"]
        include_all_types = "*" in include_types
        remaining = [max_nodes]
        truncated = [False]

        def build(node: str, level: int) -> dict:
            """Build one hierarchy branch within the shared node budget."""
            remaining[0] -= 1
            item = {
                "name": node.rsplit("|", 1)[-1],
                "type": cmds.nodeType(node),
                "children": [],
                "hidden_child_count": 0,
            }
            all_children = cmds.listRelatives(node, children=True, fullPath=True) or []
            children = []
            for child in all_children:
                if not include_shapes and cmds.objectType(child, isAType="shape"):
                    continue
                if not include_all_types and cmds.nodeType(child) not in include_types:
                    continue
                children.append(child)
            if level >= depth:
                item["hidden_child_count"] = len(children)
                truncated[0] = truncated[0] or bool(children)
                return item

            for index, child in enumerate(children):
                if remaining[0] <= 0:
                    item["hidden_child_count"] = len(children) - index
                    truncated[0] = True
                    break
                item["children"].append(build(child, level + 1))

            return item

        resolved_roots = [self._resolve_node(root) for root in roots]
        trees = []
        for root in resolved_roots:
            if remaining[0] <= 0:
                truncated[0] = True
                break
            trees.append(build(root, 0))

        return {
            "roots": [self._node_record(root) for root in resolved_roots],
            "tree": self._format_hierarchy(trees),
            "include_shapes": include_shapes,
            "include_types": include_types,
            "depth": depth,
            "node_count": max_nodes - remaining[0],
            "truncated": truncated[0],
            "scene_revision": self.revision,
        }

    def scene_connections(self, params: dict) -> dict:
        """Read a compact, directional view of direct DG connections."""
        cmds = self.cmds
        targets = params.get("targets") or cmds.ls(selection=True, long=True) or []
        if not targets:
            raise ValueError("scene_connections requires targets or an active selection")

        direction = params.get("direction", "both")
        if direction not in {"inputs", "outputs", "both"}:
            raise ValueError("direction must be inputs, outputs, or both")

        depth = min(max(params.get("depth", 1), 1), 10)
        max_connections = min(max(params.get("max_connections", 200), 1), 2_000)
        include_types = set(params.get("include_types") or [])
        exclude_types = set(params.get("exclude_types") or [])
        queue = [(self._resolve_target(target), 0) for target in targets]
        visited = set()
        seen_connections = set()
        nodes = []
        connection_count = 0
        truncated = False

        while queue:
            target, level = queue.pop(0)
            node = target.partition(".")[0]
            if target in visited:
                continue
            visited.add(target)

            item = self._typed_target(target)
            adjacent_nodes = []
            for section, source, destination in (
                ("inputs", True, False),
                ("outputs", False, True),
            ):
                if direction != "both" and section != direction:
                    continue

                connections = []
                values = cmds.listConnections(
                    target,
                    source=source,
                    destination=destination,
                    connections=True,
                    plugs=True,
                ) or []
                for index in range(0, len(values) - 1, 2):
                    local_plug, remote_plug = values[index:index + 2]
                    remote_node = remote_plug.partition(".")[0]
                    remote_type = cmds.nodeType(remote_node)
                    if include_types and remote_type not in include_types:
                        continue
                    if remote_type in exclude_types:
                        continue

                    source_plug, destination_plug = (
                        (remote_plug, local_plug)
                        if section == "inputs"
                        else (local_plug, remote_plug)
                    )
                    edge_key = (source_plug, destination_plug)
                    if edge_key in seen_connections:
                        continue
                    if connection_count >= max_connections:
                        truncated = True
                        break

                    seen_connections.add(edge_key)
                    connections.append(self._connection_item(
                        self._short_plug(source_plug),
                        self._short_plug(destination_plug),
                        cmds.nodeType(source_plug.partition(".")[0]),
                        cmds.nodeType(destination_plug.partition(".")[0]),
                    ))
                    adjacent_nodes.append(remote_node)
                    connection_count += 1

                if connections:
                    item[section] = connections
                if truncated:
                    break

            nodes.append(item)
            if truncated:
                break
            if level + 1 < depth:
                queue.extend((adjacent, level + 1) for adjacent in adjacent_nodes)

        return {
            "nodes": nodes,
            "depth": depth,
            "connection_count": connection_count,
            "truncated": truncated,
        }

    def scene_query(self, params: dict) -> dict:
        """Find scene nodes using compact structural filters."""
        cmds = self.cmds
        limit = min(params.get("limit", 200), 2_000)
        offset = max(params.get("offset", 0), 0)
        node_types = params.get("type") or []
        if isinstance(node_types, str):
            node_types = [node_types]

        if params.get("selected"):
            candidates = cmds.ls(selection=True, long=True) or []
        elif params.get("under"):
            root = self._resolve_node(params["under"])
            candidates = [root] + (cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])
        elif node_types:
            candidates = []
            for node_type in node_types:
                candidates.extend(cmds.ls(type=node_type, long=True) or [])
        else:
            candidates = cmds.ls(long=True) or []

        connected_to = params.get("connected_to")
        if connected_to:
            connected = cmds.listConnections(self._resolve_target(connected_to), source=True, destination=True) or []
            allowed = set(cmds.ls(connected, long=True) or [])
            candidates = [node for node in candidates if node in allowed]

        unique = []
        seen = set()
        for node in candidates:
            long_name = (cmds.ls(node, long=True) or [node])[0]
            if long_name in seen or not self._matches_query(long_name, params, node_types):
                continue
            seen.add(long_name)
            unique.append(long_name)

        page = unique[offset:offset + limit]
        return {
            "items": [self._node_record(node) for node in page],
            "total": len(unique),
            "offset": offset,
            "limit": limit,
            "complete": offset + len(page) >= len(unique),
            "next_offset": offset + len(page) if offset + len(page) < len(unique) else None,
            "scene_revision": self.revision,
        }

    def scene_inspect(self, params: dict) -> dict:
        """Inspect selected nodes without returning heavy geometry arrays."""
        targets = params.get("targets") or []
        if not targets:
            targets = self.cmds.ls(selection=True, long=True) or []
        if len(targets) > 100:
            raise ValueError("scene_inspect accepts at most 100 targets")

        preset = params.get("preset", "standard")
        if preset not in {"summary", "standard", "deep"}:
            raise ValueError("preset must be summary, standard, or deep")

        items = [self._inspect_node(self._resolve_node(target), preset) for target in targets]
        return {"items": items, "scene_revision": self.revision, "complete": True}

    def scene_graph(self, params: dict) -> dict:
        """Traverse DAG, DG, or construction history from root nodes."""
        roots = params.get("roots") or []
        if not roots:
            raise ValueError("scene_graph requires at least one root")

        graph_type = params.get("graph", "dg")
        direction = params.get("direction", "both")
        depth = min(max(params.get("depth", 2), 0), 20)
        max_nodes = min(params.get("max_nodes", 200), 2_000)
        root_nodes = [self._resolve_node(root) for root in roots]

        visited = set(root_nodes)
        queue = [(node, 0) for node in root_nodes]
        edges = []
        while queue and len(visited) < max_nodes:
            node, level = queue.pop(0)
            if level >= depth:
                continue

            neighbours, node_edges = self._graph_neighbours(node, graph_type, direction)
            edges.extend(node_edges)
            for neighbour in neighbours:
                resolved = self._resolve_node(neighbour)
                if resolved not in visited:
                    visited.add(resolved)
                    queue.append((resolved, level + 1))

        return {
            "nodes": [self._node_record(node) for node in sorted(visited)],
            "edges": self._unique_dicts(edges),
            "roots": [self._node_record(node) for node in root_nodes],
            "graph": graph_type,
            "direction": direction,
            "complete": not queue,
            "truncated": bool(queue),
            "scene_revision": self.revision,
        }

    def scene_patch(self, params: dict) -> dict:
        """Validate or atomically apply a list of scene edit operations."""
        operations = params.get("operations") or []
        if not operations:
            raise ValueError("scene_patch requires at least one operation")
        if len(operations) > 500:
            raise ValueError("scene_patch accepts at most 500 operations")

        expected_revision = params.get("expected_revision")
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError(f"Scene revision changed: expected {expected_revision}, current {self.revision}")

        unsupported = [operation.get("op") for operation in operations if operation.get("op") not in PATCH_OPERATIONS]
        if unsupported:
            raise ValueError(f"Unsupported patch operations: {unsupported}")

        summary = self._patch_summary(operations)
        if params.get("dry_run", True):
            return {
                "valid": True,
                "dry_run": True,
                "summary": summary,
                "operations": operations,
                "scene_revision": self.revision,
            }

        label = params.get("label", "Maya MCP patch")
        revision_before = self.revision
        temp_ids = {}
        changes = []
        chunk_open = False
        self.cmds.undoInfo(openChunk=True, chunkName=f"MCP: {label}")
        chunk_open = True

        try:
            for operation in operations:
                changes.append(self._apply_operation(operation, temp_ids))
        except Exception:
            self.cmds.undoInfo(closeChunk=True)
            chunk_open = False
            self.cmds.undo()
            raise
        finally:
            if chunk_open:
                self.cmds.undoInfo(closeChunk=True)

        self.revision += 1
        return {
            "applied": True,
            "label": label,
            "revision_before": revision_before,
            "revision_after": self.revision,
            "summary": summary,
            "changes": changes,
            "created": {key: self._node_record(value) for key, value in temp_ids.items()},
        }

    def scene_verify(self, params: dict) -> dict:
        """Evaluate structural assertions against the current scene."""
        checks = params.get("checks") or []
        results = []
        for check in checks:
            try:
                passed, actual = self._evaluate_check(check)
                results.append({"check": check, "passed": passed, "actual": actual})
            except (RuntimeError, ValueError) as exc:
                results.append({"check": check, "passed": False, "error": str(exc)})

        return {
            "passed": all(result["passed"] for result in results),
            "results": results,
            "scene_revision": self.revision,
        }

    def viewport_capture(self, params: dict) -> dict:
        """Capture one frame from a Maya model panel as a PNG image."""
        cmds = self.cmds
        width, height = self._validate_capture_size(
            params.get("width", 1280),
            params.get("height", 720),
        )
        panel = params.get("panel") or cmds.getPanel(withFocus=True)
        if cmds.getPanel(typeOf=panel) != "modelPanel":
            panels = cmds.getPanel(visiblePanels=True) or []
            panel = next((name for name in panels if cmds.getPanel(typeOf=name) == "modelPanel"), None)
        if panel is None:
            raise RuntimeError("No visible Maya model panel is available")

        original_camera = cmds.modelPanel(panel, query=True, camera=True)
        camera = params.get("camera")
        if camera:
            camera = self._resolve_node(camera)
            cmds.lookThru(panel, camera)

        file_descriptor, image_path = tempfile.mkstemp(prefix="maya_mcp_", suffix=".png")
        os.close(file_descriptor)
        os.unlink(image_path)
        try:
            cmds.playblast(
                completeFilename=image_path,
                format="image",
                compression="png",
                frame=cmds.currentTime(query=True),
                viewer=False,
                forceOverwrite=True,
                offScreen=params.get("off_screen", True),
                showOrnaments=params.get("show_ornaments", False),
                editorPanelName=panel,
                percent=100,
                widthHeight=(width, height),
            )
            with open(image_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode("ascii")
        finally:
            if camera:
                cmds.lookThru(panel, original_camera)
            if os.path.exists(image_path):
                os.unlink(image_path)

        return {
            "image_base64": image_data,
            "mime_type": "image/png",
            "width": width,
            "height": height,
            "panel": panel,
            "camera": camera or original_camera,
            "frame": cmds.currentTime(query=True),
            "scene_revision": self.revision,
        }

    def maya_command(self, params: dict) -> dict:
        """Execute one or more maya.cmds calls in a single undo chunk."""
        requests = params.get("commands")
        is_batch = requests is not None
        if requests is None:
            requests = [{
                "command": params["command"],
                "args": params.get("args") or [],
                "kwargs": params.get("kwargs") or {},
            }]

        revision_before = self.revision
        chunk_open = False
        label = "batch" if is_batch else requests[0]["command"]
        self.cmds.undoInfo(openChunk=True, chunkName=f"MCP: {label}")
        chunk_open = True

        try:
            results = []
            for request in requests:
                command_name = request["command"]
                if re.fullmatch(r"[A-Za-z_]\w*", command_name) is None or command_name.startswith("_"):
                    raise ValueError("Maya command must be a public maya.cmds command name")

                command = getattr(self.cmds, command_name)
                value = command(*(request.get("args") or []), **(request.get("kwargs") or {}))
                results.append({"command": command_name, "result": self._json_value(value)})
        except Exception:
            self.cmds.undoInfo(closeChunk=True)
            chunk_open = False
            self.cmds.undo()
            raise
        finally:
            if chunk_open:
                self.cmds.undoInfo(closeChunk=True)

        self.revision += 1
        response = {
            "selection": [self._node_record(node) for node in self.cmds.ls(selection=True, long=True) or []],
            "revision_before": revision_before,
            "revision_after": self.revision,
        }
        if is_batch:
            response["results"] = results
        else:
            response.update(results[0])

        return response

    def _matches_query(self, node: str, params: dict, node_types: list[str]) -> bool:
        """Return whether a node satisfies query filters."""
        cmds = self.cmds
        short_name = node.rsplit("|", 1)[-1]
        pattern = params.get("name")
        if pattern and not fnmatch.fnmatchcase(short_name, pattern):
            return False
        regex = params.get("regex")
        if regex and re.search(regex, short_name) is None:
            return False
        if node_types and cmds.nodeType(node) not in node_types:
            return False
        namespace = params.get("namespace")
        if namespace and not short_name.startswith(f"{namespace}:"):
            return False
        if "referenced" in params and cmds.referenceQuery(node, isNodeReferenced=True) != params["referenced"]:
            return False
        if params.get("has_attribute") and not cmds.attributeQuery(params["has_attribute"], node=node, exists=True):
            return False
        if params.get("animated") and not (cmds.listConnections(node, source=True, type="animCurve") or []):
            return False

        return True

    def _inspect_node(self, node: str, preset: str) -> dict:
        """Build one compact node inspection record."""
        cmds = self.cmds
        item = self._node_record(node)
        item["referenced"] = cmds.referenceQuery(node, isNodeReferenced=True)
        item["locked"] = bool(cmds.lockNode(node, query=True, lock=True)[0])
        item["parents"] = [self._node_record(value) for value in cmds.listRelatives(node, parent=True, fullPath=True) or []]
        item["children"] = [self._node_record(value) for value in cmds.listRelatives(node, children=True, fullPath=True) or []]
        item["history"] = [self._node_record(value) for value in cmds.listHistory(node) or []]

        if preset == "summary":
            return item

        attributes = set(cmds.listAttr(node, userDefined=True) or [])
        attributes.update(cmds.listAttr(node, keyable=True) or [])
        if preset == "deep":
            attributes.update(cmds.listAttr(node) or [])

        item["attributes"] = [self._attribute_record(node, name) for name in sorted(attributes)]
        item["connections"] = self._connection_records(node)
        return item

    def _attribute_record(self, node: str, attribute: str) -> dict:
        """Return metadata and a safe value for one attribute."""
        cmds = self.cmds
        plug = f"{node}.{attribute}"
        attribute_type = cmds.getAttr(plug, type=True)
        record = {
            "name": attribute,
            "plug": plug,
            "type": attribute_type,
            "locked": cmds.getAttr(plug, lock=True),
            "keyable": cmds.getAttr(plug, keyable=True),
            "settable": cmds.getAttr(plug, settable=True),
            "multi": cmds.attributeQuery(attribute, node=node, multi=True),
        }
        if record["multi"]:
            record["logical_indices"] = cmds.getAttr(plug, multiIndices=True) or []
        if attribute_type in HEAVY_ATTRIBUTE_TYPES:
            record["value"] = {"omitted": True, "reason": "heavy_data"}
        elif not record["multi"]:
            try:
                record["value"] = cmds.getAttr(plug)
            except RuntimeError as exc:
                record["value"] = {"unavailable": True, "reason": str(exc)}

        return record

    def _connection_records(self, node: str) -> list[dict]:
        """Return direct incoming and outgoing plug connections."""
        cmds = self.cmds
        records = []
        incoming = cmds.listConnections(node, source=True, destination=False, connections=True, plugs=True) or []
        outgoing = cmds.listConnections(node, source=False, destination=True, connections=True, plugs=True) or []
        for values, direction in ((incoming, "incoming"), (outgoing, "outgoing")):
            for index in range(0, len(values) - 1, 2):
                records.append({"local": values[index], "remote": values[index + 1], "direction": direction})

        return self._unique_dicts(records)

    def _graph_neighbours(self, node: str, graph_type: str, direction: str) -> tuple[list[str], list[dict]]:
        """Return adjacent nodes and edges for one graph traversal step."""
        cmds = self.cmds
        neighbours = []
        edges = []
        if graph_type == "dag":
            if direction in {"upstream", "both"}:
                for parent in cmds.listRelatives(node, parent=True, fullPath=True) or []:
                    neighbours.append(parent)
                    edges.append({"source": parent, "destination": node, "kind": "parent"})
            if direction in {"downstream", "both"}:
                for child in cmds.listRelatives(node, children=True, fullPath=True) or []:
                    neighbours.append(child)
                    edges.append({"source": node, "destination": child, "kind": "parent"})
        elif graph_type == "history":
            for history_node in cmds.listHistory(node, future=direction == "downstream") or []:
                neighbours.append(history_node)
                edges.append({"source": history_node, "destination": node, "kind": "history"})
        elif graph_type == "dg":
            if direction in {"upstream", "both"}:
                neighbours.extend(cmds.listConnections(node, source=True, destination=False) or [])
            if direction in {"downstream", "both"}:
                neighbours.extend(cmds.listConnections(node, source=False, destination=True) or [])
            for record in self._connection_records(node):
                if direction != "both" and record["direction"] != ("incoming" if direction == "upstream" else "outgoing"):
                    continue
                source = record["remote"] if record["direction"] == "incoming" else record["local"]
                destination = record["local"] if record["direction"] == "incoming" else record["remote"]
                edges.append({"source": source, "destination": destination, "kind": "connection"})
        else:
            raise ValueError("graph must be dag, dg, or history")

        return neighbours, edges

    def _apply_operation(self, operation: dict, temp_ids: dict) -> dict:
        """Apply one normalized patch operation."""
        cmds = self.cmds
        op = operation["op"]
        if op == "create_node":
            kwargs = {"skipSelect": True}
            if operation.get("name"):
                kwargs["name"] = operation["name"]
            node = cmds.createNode(operation["type"], **kwargs)
            if operation.get("parent"):
                node = cmds.parent(node, self._resolve_target(operation["parent"], temp_ids))[0]
            if operation.get("temp_id"):
                temp_ids[operation["temp_id"]] = node
            return {"op": op, "node": self._node_record(node)}
        if op == "delete_node":
            node = self._resolve_target(operation["target"], temp_ids)
            record = self._node_record(node)
            cmds.delete(node)
            return {"op": op, "node": record}
        if op == "rename_node":
            node = self._resolve_target(operation["target"], temp_ids)
            renamed = cmds.rename(node, operation["name"])
            return {"op": op, "node": self._node_record(renamed)}
        if op == "parent":
            child = self._resolve_target(operation["child"], temp_ids)
            parent = operation.get("parent")
            kwargs = {"absolute": operation.get("preserve_world", True)}
            result = cmds.parent(child, self._resolve_target(parent, temp_ids), **kwargs) if parent else cmds.parent(child, world=True, **kwargs)
            return {"op": op, "node": self._node_record(result[0])}
        if op == "add_attr":
            node = self._resolve_target(operation["target"], temp_ids)
            kwargs = dict(operation.get("options", {}))
            kwargs["longName"] = operation["name"]
            cmds.addAttr(node, **kwargs)
            return {"op": op, "plug": f"{node}.{operation['name']}"}
        if op == "remove_attr":
            plug = self._resolve_target(operation["target"], temp_ids)
            cmds.deleteAttr(plug)
            return {"op": op, "plug": plug}
        if op == "set_attr":
            plug = self._resolve_target(operation["target"], temp_ids)
            self._set_attribute(plug, operation["value"], operation.get("type"))
            return {"op": op, "plug": plug, "value": self._json_value(cmds.getAttr(plug))}
        if op == "reset_attr":
            plug = self._resolve_target(operation["target"], temp_ids)
            node, attribute = plug.split(".", 1)
            default = cmds.attributeQuery(attribute, node=node, listDefault=True)
            if default is None:
                raise ValueError(f"Attribute has no queryable default: {plug}")
            self._set_attribute(plug, default[0] if len(default) == 1 else default)
            return {"op": op, "plug": plug, "value": self._json_value(cmds.getAttr(plug))}
        if op == "connect":
            source = self._resolve_target(operation["source"], temp_ids)
            destination = self._resolve_target(operation["destination"], temp_ids)
            cmds.connectAttr(source, destination, force=operation.get("force", False))
            return {"op": op, "source": source, "destination": destination}
        if op == "disconnect":
            source = self._resolve_target(operation["source"], temp_ids)
            destination = self._resolve_target(operation["destination"], temp_ids)
            cmds.disconnectAttr(source, destination)
            return {"op": op, "source": source, "destination": destination}
        raise ValueError(f"Unsupported operation: {op}")

    def _evaluate_check(self, check: dict) -> tuple[bool, object]:
        """Evaluate one scene verification check."""
        cmds = self.cmds
        if "exists" in check:
            exists = bool(cmds.objExists(self._resolve_target(check["exists"], allow_missing=True)))
            return exists, exists
        if "not_exists" in check:
            exists = bool(cmds.objExists(self._resolve_target(check["not_exists"], allow_missing=True)))
            return not exists, exists
        if "type_is" in check:
            data = check["type_is"]
            actual = cmds.nodeType(self._resolve_node(data["target"]))
            return actual == data["type"], actual
        if "parent_is" in check:
            data = check["parent_is"]
            node = self._resolve_node(data["node"])
            expected = self._resolve_node(data["parent"])
            actual = cmds.listRelatives(node, parent=True, fullPath=True) or []
            return actual == [expected], actual
        if "connected" in check or "disconnected" in check:
            key = "connected" if "connected" in check else "disconnected"
            source, destination = [self._resolve_target(value) for value in check[key]]
            actual = cmds.isConnected(source, destination)
            return (actual if key == "connected" else not actual), actual
        if "attr_equals" in check:
            data = check["attr_equals"]
            actual = cmds.getAttr(self._resolve_target(data["plug"]))
            expected = data["value"]
            tolerance = data.get("tolerance", 0.0)
            if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
                return abs(actual - expected) <= tolerance, actual
            return actual == expected, actual

        raise ValueError(f"Unknown verification check: {check}")

    def _resolve_node(self, identifier: str) -> str:
        """Resolve a Maya name or uuid: identifier to one canonical node."""
        nodes = self.cmds.ls(identifier[5:] if identifier.startswith("uuid:") else identifier, long=True) or []
        if not nodes:
            raise ValueError(f"Maya node does not exist: {identifier}")
        if len(nodes) > 1:
            raise ValueError(f"Maya node is ambiguous: {identifier}")
        return nodes[0]

    def _resolve_target(self, target: str, temp_ids: dict | None = None, allow_missing: bool = False) -> str:
        """Resolve a node or plug target, including patch temporary ids."""
        temp_ids = temp_ids or {}
        node_part, separator, attribute = target.partition(".")
        if node_part.startswith("$"):
            key = node_part[1:]
            if key not in temp_ids:
                raise ValueError(f"Unknown temporary node id: {node_part}")
            node = temp_ids[key]
        elif allow_missing and not self.cmds.objExists(target):
            return target
        else:
            node = self._resolve_node(node_part)

        return f"{node}.{attribute}" if separator else node

    def _node_record(self, node: str) -> dict:
        """Return stable identity fields for one Maya node."""
        cmds = self.cmds
        long_name = (cmds.ls(node, long=True) or [node])[0]
        uuids = cmds.ls(node, uuid=True) or []
        return {
            "id": f"uuid:{uuids[0]}" if uuids else None,
            "name": long_name.rsplit("|", 1)[-1],
            "path": long_name,
            "type": cmds.nodeType(node),
        }

    def _typed_target(self, target: str) -> dict:
        """Format a node or plug with its Maya node type."""
        node = target.partition(".")[0]
        return {f"target ({self.cmds.nodeType(node)})": self._short_plug(target)}

    def _set_attribute(self, plug: str, value, attribute_type: str | None = None) -> None:
        """Set a scalar, string, or simple compound attribute value."""
        if attribute_type:
            values = value if isinstance(value, list) else [value]
            self.cmds.setAttr(plug, *values, type=attribute_type)
        elif isinstance(value, list):
            self.cmds.setAttr(plug, *value)
        else:
            self.cmds.setAttr(plug, value)

    @staticmethod
    def _naming_summary(
        names: list[str],
        max_examples: int = 3,
        prefix_limit: int = 15,
        suffix_limit: int = 20,
        family_limit: int = 20,
    ) -> dict:
        """Aggregate underscore-delimited prefixes, suffixes, and families."""
        prefixes = Counter()
        suffixes = Counter()
        families = Counter()
        family_examples = {}

        for name in names:
            bare_name = name.rsplit(":", 1)[-1]
            tokens = [token for token in bare_name.split("_") if token]
            if not tokens:
                continue

            prefixes[tokens[0]] += 1
            suffixes[tokens[-1]] += 1
            if len(tokens) >= 2:
                family = "_".join(tokens[:2])
                families[family] += 1
                family_examples.setdefault(family, [])
                if len(family_examples[family]) < max_examples:
                    family_examples[family].append(name)

        def records(counter: Counter, limit: int, minimum: int = 1) -> list[dict]:
            """Convert the most common counter values into compact records."""
            return [
                {"name": name, "count": count}
                for name, count in counter.most_common(limit)
                if count >= minimum
            ]

        family_records = records(families, family_limit, minimum=2)
        for record in family_records:
            record["examples"] = family_examples[record["name"]]

        return {
            "node_count": len(names),
            "prefixes": records(prefixes, prefix_limit),
            "suffixes": records(suffixes, suffix_limit),
            "families": family_records,
            "family_count": sum(1 for count in families.values() if count >= 2),
        }

    @staticmethod
    def _format_hierarchy(trees: list[dict]) -> str:
        """Format hierarchy records as a compact human-readable tree."""
        lines = []

        def append(item: dict, prefix: str = "", branch: str = "") -> None:
            """Append one hierarchy branch and its visible descendants."""
            lines.append(f"{prefix}{branch}{item['name']} [{item['type']}]")
            entries = [(child, False) for child in item["children"]]
            if item["hidden_child_count"]:
                entries.append((item["hidden_child_count"], True))

            child_prefix = prefix + ("   " if branch == "└─ " else "│  " if branch else "")
            for index, (value, hidden) in enumerate(entries):
                child_branch = "└─ " if index == len(entries) - 1 else "├─ "
                if hidden:
                    lines.append(f"{child_prefix}{child_branch}… {value} more")
                else:
                    append(value, child_prefix, child_branch)

        for index, tree in enumerate(trees):
            if index:
                lines.append("")
            append(tree)

        return "\n".join(lines)

    @staticmethod
    def _short_plug(plug: str) -> str:
        """Remove a DAG path while preserving the attribute path."""
        node, separator, attribute = plug.partition(".")
        short_node = node.rsplit("|", 1)[-1]
        return f"{short_node}.{attribute}" if separator else short_node

    @staticmethod
    def _connection_item(
        source: str,
        destination: str,
        source_type: str,
        destination_type: str,
    ) -> dict:
        """Format one connection without separate type fields."""
        return {
            f"source ({source_type})": source,
            f"destination ({destination_type})": destination,
        }

    @staticmethod
    def _patch_summary(operations: list[dict]) -> dict:
        """Count patch operations by operation name."""
        summary = {}
        for operation in operations:
            name = operation["op"]
            summary[name] = summary.get(name, 0) + 1
        return summary

    @staticmethod
    def _unique_dicts(items: list[dict]) -> list[dict]:
        """Remove duplicate dictionaries while preserving order."""
        unique = []
        seen = set()
        for item in items:
            key = tuple(sorted(item.items()))
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    @staticmethod
    def _json_value(value):
        """Convert common Maya tuple results to JSON-friendly lists."""
        if isinstance(value, dict):
            return {key: MayaSceneService._json_value(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [MayaSceneService._json_value(item) for item in value]
        if isinstance(value, list):
            return [MayaSceneService._json_value(item) for item in value]
        return value

    @staticmethod
    def _validate_capture_size(width: int, height: int) -> tuple[int, int]:
        """Validate viewport dimensions accepted by the MCP image tool."""
        if not 64 <= width <= 2_048 or not 64 <= height <= 2_048:
            raise ValueError("Viewport width and height must be between 64 and 2048")
        if width % 4 or height % 4:
            raise ValueError("Viewport width and height must be divisible by 4")

        return width, height
