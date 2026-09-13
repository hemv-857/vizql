"""Mermaid diagram renderer for execution plans."""

from typing import Any
from vizql.planner import PlanNode, PlanNodeType
from vizql.advisor import AdvisorResult


# Node type -> Mermaid shape/style mapping
NODE_STYLES = {
    PlanNodeType.SEQ_SCAN: ("rect", "fill:#ff6b6b,color:#fff"),
    PlanNodeType.INDEX_SCAN: ("rect", "fill:#4ecdc4,color:#fff"),
    PlanNodeType.BITMAP_SCAN: ("rect", "fill:#4ecdc4,color:#fff"),
    PlanNodeType.HASH_JOIN: ("diamond", "fill:#ffe66d,color:#000"),
    PlanNodeType.NESTED_LOOP_JOIN: ("diamond", "fill:#ffa500,color:#fff"),
    PlanNodeType.MERGE_JOIN: ("diamond", "fill:#ffd93d,color:#000"),
    PlanNodeType.HASH_AGGREGATE: ("hexagon", "fill:#a8e6cf,color:#000"),
    PlanNodeType.SORT_AGGREGATE: ("hexagon", "fill:#a8e6cf,color:#000"),
    PlanNodeType.GROUP_AGGREGATE: ("hexagon", "fill:#a8e6cf,color:#000"),
    PlanNodeType.SORT: ("parallelogram", "fill:#ffd3b6,color:#000"),
    PlanNodeType.LIMIT: ("parallelogram", "fill:#ffaaa5,color:#000"),
    PlanNodeType.CTE_SCAN: ("circle", "fill:#d4a5ff,color:#fff"),
    PlanNodeType.SUBQUERY_SCAN: ("circle", "fill:#d4a5ff,color:#fff"),
    PlanNodeType.MATERIALIZE: ("circle", "fill:#b5e7a0,color:#000"),
    PlanNodeType.INSERT: ("rect", "fill:#ff8b94,color:#fff"),
    PlanNodeType.UPDATE: ("rect", "fill:#ff8b94,color:#fff"),
    PlanNodeType.DELETE: ("rect", "fill:#ff8b94,color:#fff"),
    PlanNodeType.RESULT: ("rect", "fill:#b5e7a0,color:#000"),
    PlanNodeType.UNKNOWN: ("rect", "fill:#ccc,color:#000"),
}


def _get_shape(node_type: PlanNodeType) -> str:
    """Get Mermaid shape for node type."""
    return NODE_STYLES.get(node_type, ("rect", ""))[0]


def _get_style(node_type: PlanNodeType) -> str:
    """Get Mermaid style for node type."""
    return NODE_STYLES.get(node_type, ("", ""))[1]


def _escape_mermaid(text: str) -> str:
    """Escape text for Mermaid."""
    return text.replace('"', '\\"').replace("[", "\\[").replace("]", "\\]")


def _format_details(details: dict[str, Any]) -> str:
    """Format details dict for Mermaid label."""
    parts = []
    for k, v in details.items():
        if k in ("est_memory_mb", "est_cpu_cost", "est_io_cost", "est_rows", "est_cost"):
            continue  # Shown separately
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}={v}")
    return "<br/>".join(parts)


def render_mermaid(
    root: PlanNode,
    advisor: AdvisorResult | None = None,
) -> str:
    """Render plan as Mermaid flowchart.

    Args:
        root: Root plan node
        advisor: Optional advisor result for warnings/suggestions

    Returns:
        Mermaid diagram as string
    """
    lines = ["```mermaid", "flowchart TD"]

    node_counter = [0]
    node_ids = {}

    def get_id(node: PlanNode) -> str:
        if node not in node_ids:
            node_counter[0] += 1
            node_ids[node] = f"N{node_counter[0]}"
        return node_ids[node]

    def add_node(node: PlanNode):
        nid = get_id(node)
        shape = _get_shape(node.type)
        style = _get_style(node.type)

        # Build label
        label_parts = [node.name]
        if node.est_rows > 0:
            label_parts.append(f"Rows: {node.est_rows:,.0f}")
        if node.est_cost > 0:
            label_parts.append(f"Cost: {node.est_cost:,.0f}")
        if node.details.get("est_memory_mb", 0) > 0:
            label_parts.append(f"Mem: {node.details['est_memory_mb']:.0f}MB")

        details_str = _format_details(node.details)
        if details_str:
            label_parts.append(details_str)

        label = "<br/>".join(label_parts)
        label = _escape_mermaid(label)

        # Node definition with shape
        if shape == "rect":
            lines.append(f'    {nid}["{label}"]')
        elif shape == "diamond":
            lines.append(f'    {nid}{{"{label}"}}')
        elif shape == "hexagon":
            lines.append(f'    {nid}[[ "{label}" ]]')
        elif shape == "parallelogram":
            lines.append(f'    {nid}[/"{label}"/]')
        elif shape == "circle":
            lines.append(f'    {nid}(("{label}"))')
        else:
            lines.append(f'    {nid}["{label}"]')

        # Style
        if style:
            lines.append(f'    style {nid} {style}')

        # Recurse children
        for child in node.children:
            add_node(child)
            lines.append(f"    {get_id(child)} --> {nid}")

    add_node(root)

    # Add advisor info as subgraph if available
    if advisor and (advisor.warnings or advisor.suggestions):
        lines.append("")
        lines.append("    subgraph ADVISOR [Advisor]")
        lines.append("        direction TB")

        if advisor.warnings:
            for i, w in enumerate(advisor.warnings):
                wid = f"WARN{i}"
                lines.append(f'        {wid}["⚠️ {_escape_mermaid(w)}"]')
                lines.append(f'        style {wid} fill:#fff3cd,color:#856404')

        if advisor.suggestions:
            for i, s in enumerate(advisor.suggestions):
                sid = f"SUGG{i}"
                lines.append(f'        {sid}["💡 {_escape_mermaid(s)}"]')
                lines.append(f'        style {sid} fill:#d1ecf1,color:#0c5460')

        lines.append("    end")

    lines.append("```")
    return "\n".join(lines)


def render_mermaid_timeline(frames: list) -> str:
    """Render execution timeline as Mermaid Gantt chart."""
    lines = ["```mermaid", "gantt", "    title Execution Timeline", "    dateFormat  X", "    axisFormat  %S"]

    for frame in frames:
        if frame.status == "complete":
            status = "done"
        elif frame.status == "warning":
            status = "crit"
        else:
            status = "active"

        duration = max(1, int(frame.progress * 10))
        lines.append(f'    {frame.node_name} :{status}, {frame.step}, {duration}')

    lines.append("```")
    return "\n".join(lines)