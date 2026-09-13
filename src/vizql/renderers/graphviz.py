"""Graphviz renderer for execution plans."""

from typing import Any
from vizql.planner import PlanNode, PlanNodeType
from vizql.advisor import AdvisorResult


# Node type -> Graphviz attributes
NODE_ATTRS = {
    PlanNodeType.SEQ_SCAN: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#ff6b6b",
        "fontcolor": "white",
        "color": "#ff6b6b",
    },
    PlanNodeType.INDEX_SCAN: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#4ecdc4",
        "fontcolor": "white",
        "color": "#4ecdc4",
    },
    PlanNodeType.BITMAP_SCAN: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#4ecdc4",
        "fontcolor": "white",
        "color": "#4ecdc4",
    },
    PlanNodeType.HASH_JOIN: {
        "shape": "diamond",
        "style": "filled",
        "fillcolor": "#ffe66d",
        "fontcolor": "black",
        "color": "#ffe66d",
    },
    PlanNodeType.NESTED_LOOP_JOIN: {
        "shape": "diamond",
        "style": "filled",
        "fillcolor": "#ffa500",
        "fontcolor": "white",
        "color": "#ffa500",
    },
    PlanNodeType.MERGE_JOIN: {
        "shape": "diamond",
        "style": "filled",
        "fillcolor": "#ffd93d",
        "fontcolor": "black",
        "color": "#ffd93d",
    },
    PlanNodeType.HASH_AGGREGATE: {
        "shape": "hexagon",
        "style": "filled",
        "fillcolor": "#a8e6cf",
        "fontcolor": "black",
        "color": "#a8e6cf",
    },
    PlanNodeType.SORT_AGGREGATE: {
        "shape": "hexagon",
        "style": "filled",
        "fillcolor": "#a8e6cf",
        "fontcolor": "black",
        "color": "#a8e6cf",
    },
    PlanNodeType.GROUP_AGGREGATE: {
        "shape": "hexagon",
        "style": "filled",
        "fillcolor": "#a8e6cf",
        "fontcolor": "black",
        "color": "#a8e6cf",
    },
    PlanNodeType.SORT: {
        "shape": "parallelogram",
        "style": "filled",
        "fillcolor": "#ffd3b6",
        "fontcolor": "black",
        "color": "#ffd3b6",
    },
    PlanNodeType.LIMIT: {
        "shape": "parallelogram",
        "style": "filled",
        "fillcolor": "#ffaaa5",
        "fontcolor": "black",
        "color": "#ffaaa5",
    },
    PlanNodeType.CTE_SCAN: {
        "shape": "circle",
        "style": "filled",
        "fillcolor": "#d4a5ff",
        "fontcolor": "white",
        "color": "#d4a5ff",
    },
    PlanNodeType.SUBQUERY_SCAN: {
        "shape": "circle",
        "style": "filled",
        "fillcolor": "#d4a5ff",
        "fontcolor": "white",
        "color": "#d4a5ff",
    },
    PlanNodeType.MATERIALIZE: {
        "shape": "circle",
        "style": "filled",
        "fillcolor": "#b5e7a0",
        "fontcolor": "black",
        "color": "#b5e7a0",
    },
    PlanNodeType.INSERT: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#ff8b94",
        "fontcolor": "white",
        "color": "#ff8b94",
    },
    PlanNodeType.UPDATE: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#ff8b94",
        "fontcolor": "white",
        "color": "#ff8b94",
    },
    PlanNodeType.DELETE: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#ff8b94",
        "fontcolor": "white",
        "color": "#ff8b94",
    },
    PlanNodeType.RESULT: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#b5e7a0",
        "fontcolor": "black",
        "color": "#b5e7a0",
    },
    PlanNodeType.UNKNOWN: {
        "shape": "box",
        "style": "filled",
        "fillcolor": "#cccccc",
        "fontcolor": "black",
        "color": "#cccccc",
    },
}


def _escape_dot(text: str) -> str:
    """Escape text for Graphviz DOT."""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def _format_label(node: PlanNode) -> str:
    """Format node label for Graphviz."""
    parts = [node.name]

    if node.est_rows > 0:
        parts.append(f"Rows: {node.est_rows:,.0f}")
    if node.est_cost > 0:
        parts.append(f"Cost: {node.est_cost:,.0f}")
    if node.details.get("est_memory_mb", 0) > 0:
        parts.append(f"Mem: {node.details['est_memory_mb']:.0f}MB")

    # Add key details
    for k, v in node.details.items():
        if k in ("est_memory_mb", "est_cpu_cost", "est_io_cost", "est_rows", "est_cost"):
            continue
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}={v}")

    return _escape_dot("\\n".join(parts))


def render_graphviz(
    root: PlanNode,
    advisor: AdvisorResult | None = None,
    output_format: str = "dot",
) -> str:
    """Render plan as Graphviz DOT.

    Args:
        root: Root plan node
        advisor: Optional advisor result
        output_format: "dot" for DOT source, "svg" for SVG

    Returns:
        Graphviz DOT string or SVG
    """
    try:
        import graphviz
    except ImportError:
        return "// graphviz not installed. pip install graphviz"

    dot = graphviz.Digraph(
        name="execution_plan",
        format=output_format,
        graph_attr={
            "rankdir": "TB",
            "fontname": "Monospace",
            "fontsize": "10",
            "bgcolor": "transparent",
            "splines": "ortho",
            "nodesep": "0.5",
            "ranksep": "0.7",
        },
        node_attr={
            "fontname": "Monospace",
            "fontsize": "9",
            "margin": "0.1,0.05",
        },
        edge_attr={
            "fontname": "Monospace",
            "fontsize": "8",
            "arrowsize": "0.7",
        },
    )

    node_counter = [0]
    node_ids = {}

    def get_id(node: PlanNode) -> str:
        if node not in node_ids:
            node_counter[0] += 1
            node_ids[node] = f"N{node_counter[0]}"
        return node_ids[node]

    def add_node(node: PlanNode):
        nid = get_id(node)
        attrs = NODE_ATTRS.get(node.type, NODE_ATTRS[PlanNodeType.UNKNOWN]).copy()
        attrs["label"] = _format_label(node)
        dot.node(nid, **attrs)

        for child in node.children:
            add_node(child)
            # Edge from child to parent (bottom-up flow)
            dot.edge(get_id(child), nid)

    add_node(root)

    # Add advisor nodes if available
    if advisor and (advisor.warnings or advisor.suggestions):
        with dot.subgraph(name="cluster_advisor") as c:
            c.attr(
                label="Advisor",
                style="dashed",
                color="gray",
                fontname="Monospace",
                fontsize="10",
            )

            if advisor.warnings:
                for i, w in enumerate(advisor.warnings):
                    wid = f"WARN{i}"
                    c.node(wid, label=f"⚠️ {_escape_dot(w)}", shape="note",
                           style="filled", fillcolor="#fff3cd", fontcolor="#856404")

            if advisor.suggestions:
                for i, s in enumerate(advisor.suggestions):
                    sid = f"SUGG{i}"
                    c.node(sid, label=f"💡 {_escape_dot(s)}", shape="note",
                           style="filled", fillcolor="#d1ecf1", fontcolor="#0c5460")

    if output_format == "dot":
        return dot.source
    else:
        return dot.pipe(format=output_format).decode("utf-8")


def render_graphviz_svg(root: PlanNode, advisor: AdvisorResult | None = None) -> str:
    """Render plan as SVG."""
    return render_graphviz(root, advisor, output_format="svg")


def render_graphviz_png(root: PlanNode, advisor: AdvisorResult | None = None) -> bytes:
    """Render plan as PNG bytes."""
    try:
        import graphviz
    except ImportError:
        return b""

    dot = graphviz.Digraph(
        name="execution_plan",
        format="png",
        graph_attr={"rankdir": "TB", "fontname": "Monospace", "bgcolor": "white"},
        node_attr={"fontname": "Monospace"},
    )

    node_counter = [0]
    node_ids = {}

    def get_id(node: PlanNode) -> str:
        if node not in node_ids:
            node_counter[0] += 1
            node_ids[node] = f"N{node_counter[0]}"
        return node_ids[node]

    def add_node(node: PlanNode):
        nid = get_id(node)
        attrs = NODE_ATTRS.get(node.type, NODE_ATTRS[PlanNodeType.UNKNOWN]).copy()
        attrs["label"] = _format_label(node)
        dot.node(nid, **attrs)
        for child in node.children:
            add_node(child)
            dot.edge(get_id(child), nid)

    add_node(root)
    return dot.pipe(format="png")