"""Query advisor - analyzes plans for bottlenecks and suggestions."""

from dataclasses import dataclass, field
from typing import Any
from vizql.planner import PlanNode, PlanNodeType


@dataclass
class AdvisorResult:
    """Result of plan analysis."""
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    index_recommendations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "index_recommendations": self.index_recommendations,
        }


def analyze_plan(root: PlanNode, frames: list | None = None) -> AdvisorResult:
    """Analyze execution plan for issues and optimizations.

    Args:
        root: Root plan node
        frames: Optional simulation frames

    Returns:
        AdvisorResult with warnings and suggestions
    """
    result = AdvisorResult()
    _analyze_node(root, result)
    return result


def _analyze_node(node: PlanNode, result: AdvisorResult, depth: int = 0):
    """Recursively analyze plan nodes."""
    node_type = node.type

    # Sequential scan warnings
    if node_type == PlanNodeType.SEQ_SCAN:
        table = node.details.get("table", "unknown")
        rows = node.est_rows
        filter_cond = node.details.get("filter")

        if rows > 100000:
            result.warnings.append(
                f"Seq Scan on {table}: {rows:,.0f} rows scanned"
            )
            if filter_cond:
                result.suggestions.append(
                    f"CREATE INDEX ON {table}({_extract_columns(filter_cond)}) "
                    f"-- Est. impact: {_estimate_index_impact(rows):.0f}% faster"
                )
            else:
                result.suggestions.append(
                    f"Consider adding WHERE clause or partitioning for {table}"
                )

    # Hash join warnings
    elif node_type == PlanNodeType.HASH_JOIN:
        mem_mb = node.details.get("est_memory_mb", 0)
        if mem_mb > 100:
            result.warnings.append(
                f"Hash Join may spill to disk: {mem_mb:.0f}MB estimated memory"
            )
            result.suggestions.append(
                "Increase work_mem or add join condition to reduce build side"
            )

        # Check for nested loop candidate
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 0
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 0
        if left_rows < 1000 and right_rows > 10000:
            result.suggestions.append(
                "Consider Nested Loop Join for small outer table"
            )

    # Nested loop join warnings
    elif node_type == PlanNodeType.NESTED_LOOP_JOIN:
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 0
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 0
        if left_rows > 1000 and right_rows > 1000:
            result.warnings.append(
                f"Nested Loop Join on large inputs: {left_rows:,.0f} × {right_rows:,.0f}"
            )
            result.suggestions.append(
                "Add index on inner table join column or rewrite as Hash Join"
            )

    # Sort warnings
    elif node_type == PlanNodeType.SORT:
        mem_mb = node.details.get("est_memory_mb", 0)
        if mem_mb > 100:
            result.warnings.append(
                f"Sort may spill to disk: {mem_mb:.0f}MB estimated"
            )
            result.suggestions.append(
                "Increase work_mem or add ORDER BY LIMIT to use top-N sort"
            )

    # Aggregate warnings
    elif node_type in (PlanNodeType.HASH_AGGREGATE, PlanNodeType.GROUP_AGGREGATE):
        mem_mb = node.details.get("est_memory_mb", 0)
        if mem_mb > 100:
            result.warnings.append(
                f"Hash Aggregate may spill: {mem_mb:.0f}MB estimated"
            )
            result.suggestions.append(
                "Consider increasing work_mem or using approximate aggregation"
            )

        group_cols = node.details.get("group_by", [])
        if len(group_cols) > 10:
            result.warnings.append(
                f"Large GROUP BY ({len(group_cols)} columns) may be slow"
            )

    # CTE materialization
    elif node_type == PlanNodeType.CTE_SCAN:
        result.suggestions.append(
            "Consider materializing CTE if referenced multiple times"
        )

    # General cost warnings
    if node.est_cost > 50000:
        result.warnings.append(
            f"High cost operation ({node.type.value}): {node.est_cost:,.0f}"
        )

    # Index recommendations
    _recommend_indexes(node, result)

    # Recurse
    for child in node.children:
        _analyze_node(child, result, depth + 1)


def _extract_columns(condition: str) -> str:
    """Extract column names from WHERE condition (simplified)."""
    import re
    # Very basic extraction - look for column names before operators
    cols = re.findall(r'(\w+)\s*[=<>!]', condition)
    return ", ".join(cols[:3]) if cols else "column"


def _estimate_index_impact(rows: float) -> float:
    """Estimate index impact percentage."""
    if rows > 1_000_000:
        return 99
    elif rows > 100_000:
        return 95
    elif rows > 10_000:
        return 90
    else:
        return 70


def _recommend_indexes(node: PlanNode, result: AdvisorResult):
    """Recommend indexes based on query patterns."""
    # Track columns used in filters, joins, group by, order by
    filter_cols = {}
    join_cols = {}
    group_cols = {}
    order_cols = {}

    _collect_columns(node, filter_cols, join_cols, group_cols, order_cols)

    # Generate index recommendations
    for table, cols in filter_cols.items():
        if len(cols) == 1:
            col = list(cols)[0]
            result.index_recommendations.append({
                "table": table,
                "columns": [col],
                "type": "btree",
                "reason": f"WHERE {col} = ?",
                "estimated_impact": "high",
            })
        elif len(cols) > 1:
            result.index_recommendations.append({
                "table": table,
                "columns": list(cols),
                "type": "btree",
                "reason": f"WHERE {' AND '.join(f'{c} = ?' for c in cols)}",
                "estimated_impact": "high",
            })

    for table, cols in join_cols.items():
        if cols:
            result.index_recommendations.append({
                "table": table,
                "columns": list(cols),
                "type": "btree",
                "reason": f"JOIN ON {', '.join(cols)}",
                "estimated_impact": "medium",
            })


def _collect_columns(
    node: PlanNode,
    filter_cols: dict[str, set],
    join_cols: dict[str, set],
    group_cols: dict[str, set],
    order_cols: dict[str, set],
):
    """Collect column usage from plan."""
    node_type = node.type
    table = node.details.get("table")

    if node_type == PlanNodeType.SEQ_SCAN and table:
        filter_cond = node.details.get("filter")
        if filter_cond:
            cols = _extract_columns(filter_cond).split(", ")
            filter_cols.setdefault(table, set()).update(cols)

    elif node_type == PlanNodeType.HASH_JOIN and table:
        cond = node.details.get("condition", "")
        cols = _extract_columns(cond).split(", ")
        join_cols.setdefault(table, set()).update(cols)

    elif node_type in (PlanNodeType.HASH_AGGREGATE, PlanNodeType.GROUP_AGGREGATE):
        for col in node.details.get("group_by", []):
            # Need to find which table this column belongs to
            # Simplified: use first table found
            for t in filter_cols.keys():
                group_cols.setdefault(t, set()).add(col)

    elif node_type == PlanNodeType.SORT:
        for col in node.details.get("order_by", []):
            for t in filter_cols.keys():
                order_cols.setdefault(t, set()).add(col)

    for child in node.children:
        _collect_columns(child, filter_cols, join_cols, group_cols, order_cols)