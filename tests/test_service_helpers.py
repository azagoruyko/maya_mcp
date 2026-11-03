"""Maya-independent tests for scene service helpers."""

import pytest

from maya_mcp.maya_service import MayaSceneService


def test_patch_summary_counts_operations() -> None:
    """Patch summaries group operations by their public operation name."""
    operations = [
        {"op": "set_attr"},
        {"op": "connect"},
        {"op": "set_attr"},
    ]

    assert MayaSceneService._patch_summary(operations) == {"set_attr": 2, "connect": 1}


def test_json_value_converts_nested_tuples() -> None:
    """Maya tuple results become JSON-compatible lists."""
    assert MayaSceneService._json_value([(1.0, 2.0, 3.0)]) == [[1.0, 2.0, 3.0]]


def test_capture_dimensions_are_bounded_and_divisible_by_four() -> None:
    """Viewport captures use dimensions supported reliably by Maya on Windows."""
    assert MayaSceneService._validate_capture_size(1280, 720) == (1280, 720)

    with pytest.raises(ValueError, match="divisible by 4"):
        MayaSceneService._validate_capture_size(1279, 720)


def test_naming_summary_finds_repeated_families() -> None:
    """Naming summaries expose repeated module-like prefixes."""
    summary = MayaSceneService._naming_summary([
        "L_arm_ik_control",
        "L_arm_fk_control",
        "R_arm_ik_control",
        "R_arm_fk_control",
        "body_geo",
    ])

    assert summary["node_count"] == 5
    assert summary["prefixes"][0] == {"name": "L", "count": 2}
    assert summary["suffixes"][0] == {"name": "control", "count": 4}
    assert summary["families"][0]["name"] == "L_arm"
    assert summary["families"][0]["count"] == 2


def test_format_hierarchy_uses_compact_tree_lines() -> None:
    """Hierarchy formatting avoids repeated paths and identity metadata."""
    tree = {
        "name": "controls",
        "type": "transform",
        "hidden_child_count": 0,
        "children": [
            {
                "name": "L_arm_controls",
                "type": "transform",
                "hidden_child_count": 2,
                "children": [],
            },
            {
                "name": "R_arm_controls",
                "type": "transform",
                "hidden_child_count": 0,
                "children": [],
            },
        ],
    }

    assert MayaSceneService._format_hierarchy([tree]) == (
        "controls [transform]\n"
        "├─ L_arm_controls [transform]\n"
        "│  └─ … 2 more\n"
        "└─ R_arm_controls [transform]"
    )


def test_connection_item_keeps_types_next_to_endpoint_names() -> None:
    """Connection records avoid separate source and destination type fields."""
    assert MayaSceneService._connection_item(
        "M_spine_5_joint.worldMatrix",
        "L_arm_options_control_parentConstraint1.targetParentMatrix",
        "joint",
        "parentConstraint",
    ) == {
        "source (joint)": "M_spine_5_joint.worldMatrix",
        "destination (parentConstraint)": (
            "L_arm_options_control_parentConstraint1.targetParentMatrix"
        ),
    }


def test_short_plug_removes_only_the_dag_path() -> None:
    """Compact connection output keeps namespaces and full attribute paths."""
    assert MayaSceneService._short_plug(
        "|character|rig|hero:L_arm_control.worldMatrix[0]"
    ) == "hero:L_arm_control.worldMatrix[0]"
