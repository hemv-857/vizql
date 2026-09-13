"""Execution simulation - simulates row flow through plan tree."""

from dataclasses import dataclass, field
from typing import Any
from copy import deepcopy

from .planner import PlanNode, PlanNodeType
from .cost_model import get_cost_model, CostEstimate


@dataclass
class SimulationFrame:
    """A single frame in the execution animation."""
    step: int
    node_id: str
    node_type: str
    node_name: str
    input_rows: float
    output_rows: float
    progress: float  # 0.0 to 1.0
    status: str  # "pending", "running", "complete", "warning"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "node_name": self.node_name,
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "progress": self.progress,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class SimulationResult:
    """Complete simulation result."""
    frames: list[SimulationFrame]
    total_rows: float
    total_cost: float
    warnings: list[str] = field(default_factory=list)


class SimulationError(Exception):
    """Simulation error."""
    pass


def simulate_execution(
    root: PlanNode,
    dialect: str = "postgres",
    table_stats: dict | None = None,
) -> list[SimulationFrame]:
    """Simulate execution of a plan tree, producing animation frames.

    Args:
        root: Root of the plan tree
        dialect: SQL dialect for cost model
        table_stats: Optional table statistics

    Returns:
        List of SimulationFrame for animation
    """
    cost_model = get_cost_model(dialect)

    # First pass: estimate costs bottom-up
    _estimate_costs_bottom_up(root, cost_model, table_stats)

    # Second pass: generate animation frames
    frames = []
    frame_step = 0
    node_counter = 0

    def get_node_id(node: PlanNode) -> str:
        nonlocal node_counter
        if not hasattr(node, '_vizql_id'):
            node_counter += 1
            node._vizql_id = f"node_{node_counter}"
        return node._vizql_id

    def simulate_node(node: PlanNode, depth: int = 0) -> tuple[float, float]:
        """Simulate a node, returning (input_rows, output_rows)."""
        nonlocal frame_step
        node_id = get_node_id(node)

        # Simulate children first
        child_results = []
        for child in node.children:
            child_results.append(simulate_node(child, depth + 1))

        # Calculate input rows (sum of children outputs)
        if child_results:
            input_rows = sum(r[1] for r in child_results)
        else:
            input_rows = node.est_rows

        # Frame: starting this node
        frame_step += 1
        frames.append(SimulationFrame(
            step=frame_step,
            node_id=node_id,
            node_type=node.type.value,
            node_name=node.name,
            input_rows=input_rows,
            output_rows=0,
            progress=0.0,
            status="running",
            details={"depth": depth, "phase": "start"},
        ))

        # Simulate processing
        output_rows = node.est_rows

        # Progress frames (3 steps)
        for p in [0.33, 0.66, 1.0]:
            frame_step += 1
            frames.append(SimulationFrame(
                step=frame_step,
                node_id=node_id,
                node_type=node.type.value,
                node_name=node.name,
                input_rows=input_rows,
                output_rows=output_rows * p,
                progress=p,
                status="running" if p < 1.0 else "complete",
                details={"depth": depth, "phase": "processing", "progress_pct": int(p * 100)},
            ))

        # Check for warnings
        if node.est_cost > 10000:
            frames[-1].status = "warning"
            frames[-1].details["warning"] = "High cost operation"

        return input_rows, output_rows

    simulate_node(root)

    # Final summary frame
    frame_step += 1
    frames.append(SimulationFrame(
        step=frame_step,
        node_id="summary",
        node_type="SUMMARY",
        node_name="Execution Complete",
        input_rows=root.est_rows,
        output_rows=root.est_rows,
        progress=1.0,
        status="complete",
        details={"total_cost": root.est_cost, "total_rows": root.est_rows},
    ))

    return frames


def _estimate_costs_bottom_up(
    node: PlanNode,
    cost_model,
    table_stats: dict | None = None,
):
    """Estimate costs for all nodes bottom-up."""
    for child in node.children:
        _estimate_costs_bottom_up(child, cost_model, table_stats)

    # Get child row estimates
    child_rows = [c.est_rows for c in node.children] if node.children else []

    # Estimate this node
    estimate = cost_model.estimate(node, table_stats)
    node.est_rows = estimate.rows
    node.est_cost = estimate.cost
    node.details["est_memory_mb"] = estimate.memory_mb
    node.details["est_cpu_cost"] = estimate.cpu_cost
    node.details["est_io_cost"] = estimate.io_cost


def run_full_simulation(
    root: PlanNode,
    dialect: str = "postgres",
    table_stats: dict | None = None,
) -> SimulationResult:
    """Run full simulation with detailed analysis."""
    frames = simulate_execution(root, dialect, table_stats)

    warnings = []
    _collect_warnings(root, warnings)

    return SimulationResult(
        frames=frames,
        total_rows=root.est_rows,
        total_cost=root.est_cost,
        warnings=warnings,
    )


def _collect_warnings(node: PlanNode, warnings: list[str]):
    """Collect warnings from plan tree."""
    node_type = node.type

    if node_type == PlanNodeType.SEQ_SCAN:
        table = node.details.get("table", "unknown")
        rows = node.est_rows
        if rows > 100000:
            warnings.append(f"Seq Scan on {table}: {rows:,.0f} rows (consider index)")

    elif node_type == PlanNodeType.HASH_JOIN:
        mem = node.details.get("est_memory_mb", 0)
        if mem > 100:
            warnings.append(f"Hash Join may spill to disk: {mem:.0f}MB estimated")

    elif node_type == PlanNodeType.SORT:
        mem = node.details.get("est_memory_mb", 0)
        if mem > 100:
            warnings.append(f"Sort may spill to disk: {mem:.0f}MB estimated")

    elif node_type == PlanNodeType.NESTED_LOOP_JOIN:
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 0
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 0
        if left_rows > 1000 and right_rows > 1000:
            warnings.append(f"Nested Loop Join on large inputs: {left_rows:,.0f} x {right_rows:,.0f}")

    for child in node.children:
        _collect_warnings(child, warnings)