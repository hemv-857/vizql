"""Terminal renderer with rich formatting and animation support."""

from typing import Any
from rich.console import Console, Group
from rich.tree import Tree
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.live import Live
from rich import box
import time

from vizql.planner import PlanNode, PlanNodeType
from vizql.simulator import SimulationFrame, SimulationResult
from vizql.advisor import AdvisorResult


# Node type -> Rich style/emoji mapping
NODE_STYLES = {
    PlanNodeType.SEQ_SCAN: ("🔴", "red", "Seq Scan"),
    PlanNodeType.INDEX_SCAN: ("🟢", "green", "Index Scan"),
    PlanNodeType.BITMAP_SCAN: ("🟢", "green", "Bitmap Scan"),
    PlanNodeType.HASH_JOIN: ("🟡", "yellow", "Hash Join"),
    PlanNodeType.NESTED_LOOP_JOIN: ("🟠", "orange3", "Nested Loop"),
    PlanNodeType.MERGE_JOIN: ("🟡", "yellow", "Merge Join"),
    PlanNodeType.HASH_AGGREGATE: ("🔵", "blue", "Hash Agg"),
    PlanNodeType.SORT_AGGREGATE: ("🔵", "blue", "Sort Agg"),
    PlanNodeType.GROUP_AGGREGATE: ("🔵", "blue", "Group Agg"),
    PlanNodeType.SORT: ("🟣", "magenta", "Sort"),
    PlanNodeType.LIMIT: ("🟣", "magenta", "Limit"),
    PlanNodeType.CTE_SCAN: ("🟣", "purple", "CTE Scan"),
    PlanNodeType.SUBQUERY_SCAN: ("🟣", "purple", "Subquery"),
    PlanNodeType.MATERIALIZE: ("⚪", "white", "Materialize"),
    PlanNodeType.INSERT: ("🟠", "orange3", "Insert"),
    PlanNodeType.UPDATE: ("🟠", "orange3", "Update"),
    PlanNodeType.DELETE: ("🟠", "orange3", "Delete"),
    PlanNodeType.RESULT: ("🟢", "green", "Result"),
    PlanNodeType.UNKNOWN: ("⚪", "white", "Unknown"),
}


def _format_number(n: float) -> str:
    """Format number with commas."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    else:
        return f"{n:,.0f}"


def _build_tree(node: PlanNode, show_costs: bool = True) -> Tree:
    """Build a Rich Tree from plan node."""
    emoji, color, type_name = NODE_STYLES.get(node.type, ("⚪", "white", node.type.value))

    # Build label
    label_parts = [f"[{color}]{emoji} {node.name}[/{color}]"]

    if show_costs:
        if node.est_rows > 0:
            label_parts.append(f"[dim]Rows: {_format_number(node.est_rows)}[/dim]")
        if node.est_cost > 0:
            label_parts.append(f"[dim]Cost: {_format_number(node.est_cost)}[/dim]")
        mem = node.details.get("est_memory_mb", 0)
        if mem > 0:
            label_parts.append(f"[dim]Mem: {mem:.0f}MB[/dim]")

    # Add key details
    detail_parts = []
    for k, v in node.details.items():
        if k in ("est_memory_mb", "est_cpu_cost", "est_io_cost", "est_rows", "est_cost"):
            continue
        if isinstance(v, (str, int, float, bool)):
            detail_parts.append(f"{k}={v}")
    if detail_parts:
        label_parts.append(f"[dim]{', '.join(detail_parts)}[/dim]")

    label = " ".join(label_parts)
    tree = Tree(label)

    for child in node.children:
        tree.add(_build_tree(child, show_costs))

    return tree


def _render_warnings(advisor: AdvisorResult | None) -> Panel | None:
    """Render warnings panel."""
    if not advisor or not advisor.warnings:
        return None

    lines = []
    for w in advisor.warnings:
        lines.append(f"[yellow]⚠️  {w}[/yellow]")

    return Panel(
        "\n".join(lines),
        title="[yellow]Warnings[/yellow]",
        border_style="yellow",
        padding=(1, 2),
    )


def _render_suggestions(advisor: AdvisorResult | None) -> Panel | None:
    """Render suggestions panel."""
    if not advisor or not advisor.suggestions:
        return None

    lines = []
    for i, s in enumerate(advisor.suggestions, 1):
        lines.append(f"[cyan]{i}. {s}[/cyan]")

    return Panel(
        "\n".join(lines),
        title="[cyan]Suggestions[/cyan]",
        border_style="cyan",
        padding=(1, 2),
    )


def _render_summary(root: PlanNode) -> Panel:
    """Render summary panel."""
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Total Cost", _format_number(root.est_cost))
    table.add_row("Total Rows", _format_number(root.est_rows))
    table.add_row("Max Memory", f"{root.details.get('est_memory_mb', 0):.0f} MB")

    return Panel(
        table,
        title="[bold]Summary[/bold]",
        border_style="blue",
        padding=(1, 2),
    )


def render_terminal(
    root: PlanNode,
    frames: list[SimulationFrame] | None = None,
    advisor: AdvisorResult | None = None,
    show_costs: bool = True,
    show_suggestions: bool = True,
    animate: bool = False,
) -> Any:
    """Render execution plan in terminal.

    Args:
        root: Root plan node
        frames: Optional animation frames
        advisor: Optional advisor result
        show_costs: Show cost estimates
        show_suggestions: Show optimization suggestions
        animate: Show animated execution

    Returns:
        Rich renderable (Tree, Panel, or Group)
    """
    if animate and frames:
        return _render_animated(frames, advisor, show_costs, show_suggestions)

    # Static render
    tree = _build_tree(root, show_costs)
    summary = _render_summary(root)

    panels = [summary]

    if advisor:
        warn_panel = _render_warnings(advisor)
        if warn_panel:
            panels.append(warn_panel)

        if show_suggestions:
            sugg_panel = _render_suggestions(advisor)
            if sugg_panel:
                panels.append(sugg_panel)

    if len(panels) == 1:
        return Group(tree, panels[0])
    else:
        return Group(tree, Columns(panels, equal=True, expand=True))


def _render_animated(
    frames: list[SimulationFrame],
    advisor: AdvisorResult | None,
    show_costs: bool,
    show_suggestions: bool,
) -> Any:
    """Render animated execution using Rich Live."""
    # For now, return a static representation with progress indication
    # Full animation would require a Live context which can't be returned
    # This is a placeholder that shows the final state with progress bars

    # Group frames by node
    node_frames = {}
    for f in frames:
        if f.node_id not in node_frames:
            node_frames[f.node_id] = []
        node_frames[f.node_id].append(f)

    # Build tree with progress
    # We'd need the actual plan tree to map frames to nodes
    # For now, show a table of execution steps

    table = Table(title="Execution Steps", box=box.ROUNDED)
    table.add_column("Step", justify="right", style="dim")
    table.add_column("Node", style="bold")
    table.add_column("Input Rows", justify="right")
    table.add_column("Output Rows", justify="right")
    table.add_column("Progress", width=20)
    table.add_column("Status")

    for frame in frames:
        if frame.node_type == "SUMMARY":
            continue

        emoji, color, _ = NODE_STYLES.get(
            PlanNodeType(frame.node_type),
            ("⚪", "white", frame.node_type)
        )

        progress_bar = "█" * int(frame.progress * 10) + "░" * (10 - int(frame.progress * 10))

        status_style = {
            "pending": "dim",
            "running": "yellow",
            "complete": "green",
            "warning": "red",
        }.get(frame.status, "white")

        table.add_row(
            str(frame.step),
            f"[{color}]{emoji} {frame.node_name}[/{color}]",
            _format_number(frame.input_rows),
            _format_number(frame.output_rows),
            f"[{status_style}]{progress_bar}[/{status_style}]",
            f"[{status_style}]{frame.status}[/{status_style}]",
        )

    panels = [table]

    if advisor:
        warn_panel = _render_warnings(advisor)
        if warn_panel:
            panels.append(warn_panel)

        if show_suggestions:
            sugg_panel = _render_suggestions(advisor)
            if sugg_panel:
                panels.append(sugg_panel)

    return Group(*panels)


def render_plan_text(root: PlanNode, indent: int = 0, show_costs: bool = True) -> str:
    """Render plan as plain text (for logging/non-TTY)."""
    lines = []
    prefix = "  " * indent

    emoji, _, _ = NODE_STYLES.get(root.type, ("", "", ""))
    label = f"{prefix}{emoji} {root.name}"

    if show_costs:
        parts = []
        if root.est_rows > 0:
            parts.append(f"Rows={_format_number(root.est_rows)}")
        if root.est_cost > 0:
            parts.append(f"Cost={_format_number(root.est_cost)}")
        if root.details.get("est_memory_mb", 0) > 0:
            parts.append(f"Mem={root.details['est_memory_mb']:.0f}MB")
        if parts:
            label += f" ({', '.join(parts)})"

    lines.append(label)

    for child in root.children:
        lines.append(render_plan_text(child, indent + 1, show_costs))

    return "\n".join(lines)


def print_execution_animation(frames: list[SimulationFrame], delay: float = 0.5):
    """Print animated execution to console (blocking)."""
    console = Console()

    for frame in frames:
        if frame.node_type == "SUMMARY":
            continue

        emoji, color, _ = NODE_STYLES.get(
            PlanNodeType(frame.node_type),
            ("⚪", "white", frame.node_type)
        )

        progress = int(frame.progress * 100)
        bar = "█" * (progress // 5) + "░" * (20 - progress // 5)

        line = f"[{color}]{emoji} {frame.node_name}[/{color}] | {bar} {progress}% | In: {_format_number(frame.input_rows)} Out: {_format_number(frame.output_rows)}"
        console.print(line)
        time.sleep(delay)

    # Final summary
    summary_frame = next((f for f in frames if f.node_type == "SUMMARY"), None)
    if summary_frame:
        console.print(Panel(
            f"Total Rows: {_format_number(summary_frame.input_rows)}\n"
            f"Total Cost: {_format_number(summary_frame.details.get('total_cost', 0))}",
            title="[bold green]Execution Complete[/bold green]",
            border_style="green",
        ))