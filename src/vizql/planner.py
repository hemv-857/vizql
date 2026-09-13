"""Plan building - converts AST to execution plan tree."""

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum

import sqlglot
from sqlglot import exp

from .parser import ParsedQuery


class PlanNodeType(str, Enum):
    """Types of plan nodes."""
    # Scans
    SEQ_SCAN = "Seq Scan"
    INDEX_SCAN = "Index Scan"
    BITMAP_SCAN = "Bitmap Scan"
    # Joins
    NESTED_LOOP_JOIN = "Nested Loop Join"
    HASH_JOIN = "Hash Join"
    MERGE_JOIN = "Merge Join"
    # Aggregation
    HASH_AGGREGATE = "Hash Aggregate"
    SORT_AGGREGATE = "Sort Aggregate"
    GROUP_AGGREGATE = "Group Aggregate"
    # Sorting
    SORT = "Sort"
    LIMIT = "Limit"
    # Set operations
    UNION = "Union"
    INTERSECT = "Intersect"
    EXCEPT = "Except"
    # CTEs / Subqueries
    CTE_SCAN = "CTE Scan"
    SUBQUERY_SCAN = "Subquery Scan"
    # DML
    INSERT = "Insert"
    UPDATE = "Update"
    DELETE = "Delete"
    # Utility
    RESULT = "Result"
    MATERIALIZE = "Materialize"
    # Unknown
    UNKNOWN = "Unknown"


@dataclass(unsafe_hash=True)
class PlanNode:
    """A node in the execution plan tree."""
    type: PlanNodeType
    name: str = ""
    details: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    children: list["PlanNode"] = field(default_factory=list, hash=False, compare=False)
    parent: Optional["PlanNode"] = field(default=None, hash=False, compare=False)
    # Runtime estimates (filled by simulator)
    est_rows: float = 0
    est_cost: float = 0
    actual_rows: float = 0
    actual_time_ms: float = 0

    def add_child(self, child: "PlanNode") -> "PlanNode":
        child.parent = self
        self.children.append(child)
        return child

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "name": self.name,
            "details": self.details,
            "est_rows": self.est_rows,
            "est_cost": self.est_cost,
            "actual_rows": self.actual_rows,
            "actual_time_ms": self.actual_time_ms,
            "children": [c.to_dict() for c in self.children],
        }

    def __str__(self) -> str:
        return f"{self.type.value}: {self.name}"


class PlanError(Exception):
    """Plan building error."""
    pass


def infer_plan_type(node: exp.Expression, dialect: str) -> PlanNodeType:
    """Infer plan node type from AST node."""
    if isinstance(node, exp.Select):
        # Check for JOINs
        if node.args.get("joins"):
            # Will be refined by join condition analysis
            return PlanNodeType.HASH_JOIN
        # Check for GROUP BY
        if node.args.get("group"):
            return PlanNodeType.HASH_AGGREGATE
        # Check for ORDER BY
        if node.args.get("order"):
            return PlanNodeType.SORT
        # Check for LIMIT
        if node.args.get("limit"):
            return PlanNodeType.LIMIT
        # Default to scan
        return PlanNodeType.SEQ_SCAN

    elif isinstance(node, exp.Join):
        join_kind = node.kind.upper() if node.kind else "INNER"
        # Could analyze condition for merge vs hash
        return PlanNodeType.HASH_JOIN

    elif isinstance(node, exp.Table):
        return PlanNodeType.SEQ_SCAN

    elif isinstance(node, exp.AggFunc):
        return PlanNodeType.HASH_AGGREGATE

    elif isinstance(node, exp.Where):
        return PlanNodeType.SEQ_SCAN

    elif isinstance(node, exp.Order):
        return PlanNodeType.SORT

    elif isinstance(node, exp.Limit):
        return PlanNodeType.LIMIT

    elif isinstance(node, exp.CTE):
        return PlanNodeType.CTE_SCAN

    elif isinstance(node, exp.Subquery):
        return PlanNodeType.SUBQUERY_SCAN

    return PlanNodeType.UNKNOWN


def build_scan_node(table: exp.Table, dialect: str) -> PlanNode:
    """Build a scan node for a table."""
    table_name = table.name or "unknown"
    alias = table.alias or table_name

    return PlanNode(
        type=PlanNodeType.SEQ_SCAN,
        name=f"Seq Scan on {table_name}",
        details={
            "table": table_name,
            "alias": alias,
            "schema": table.args.get("db"),
        },
    )


def build_join_node(join: exp.Join, dialect: str) -> PlanNode:
    """Build a join node."""
    join_kind = join.kind.upper() if join.kind else "INNER"
    condition = join.args.get("on")

    cond_str = ""
    if condition:
        cond_str = condition.sql(dialect=dialect)

    return PlanNode(
        type=PlanNodeType.HASH_JOIN,
        name=f"Hash Join ({join_kind})",
        details={
            "join_type": join_kind,
            "condition": cond_str,
        },
    )


def build_aggregate_node(select: exp.Select, dialect: str) -> PlanNode:
    """Build an aggregate node."""
    group_by = select.args.get("group", [])
    group_cols = [g.sql(dialect=dialect) for g in group_by]

    # Find aggregate functions
    agg_funcs = []
    for expr in select.expressions:
        for agg in expr.find_all(exp.AggFunc):
            agg_funcs.append(agg.sql(dialect=dialect))

    return PlanNode(
        type=PlanNodeType.HASH_AGGREGATE,
        name="Hash Aggregate",
        details={
            "group_by": group_cols,
            "aggregates": agg_funcs,
        },
    )


def build_sort_node(select: exp.Select, dialect: str) -> PlanNode:
    """Build a sort node."""
    order_by = select.args.get("order")
    if order_by and hasattr(order_by, "expressions"):
        order_cols = [o.sql(dialect=dialect) for o in order_by.expressions]
    else:
        order_cols = []

    return PlanNode(
        type=PlanNodeType.SORT,
        name=f"Sort ({', '.join(order_cols)})",
        details={
            "order_by": order_cols,
        },
    )


def build_limit_node(select: exp.Select, dialect: str) -> PlanNode:
    """Build a limit node."""
    limit = select.args.get("limit")
    limit_val = limit.expression.sql(dialect=dialect) if limit else "?"

    return PlanNode(
        type=PlanNodeType.LIMIT,
        name=f"Limit {limit_val}",
        details={
            "limit": limit_val,
        },
    )


def build_cte_nodes(ctes: list[exp.CTE], dialect: str) -> dict[str, PlanNode]:
    """Build CTE nodes, returning map of name -> root node."""
    cte_map = {}
    for cte in ctes:
        name = cte.alias
        if not name:
            continue
        # Recursively build plan for CTE body
        cte_plan = build_plan_from_select(cte.this, dialect)
        cte_node = PlanNode(
            type=PlanNodeType.CTE_SCAN,
            name=f"CTE Scan {name}",
            details={"cte_name": name},
        )
        if cte_plan:
            cte_node.add_child(cte_plan)
        cte_map[name] = cte_node
    return cte_map


def build_plan_from_select(select: exp.Select, dialect: str) -> Optional[PlanNode]:
    """Build plan tree from a SELECT statement."""
    if not select:
        return None

    # Check for CTEs first
    ctes = list(select.find_all(exp.CTE))
    cte_map = build_cte_nodes(ctes, dialect)

    # Build main query plan
    # Start from the FROM clause
    from_clause = select.args.get("from_")
    joins = select.args.get("joins", [])

    root = None
    current = None

    # Handle table source - could be in FROM or first JOIN
    if from_clause:
        if isinstance(from_clause.this, exp.Table):
            root = build_scan_node(from_clause.this, dialect)
            current = root
        elif isinstance(from_clause.this, exp.Subquery):
            subquery_plan = build_plan_from_select(from_clause.this.this, dialect)
            root = PlanNode(
                type=PlanNodeType.SUBQUERY_SCAN,
                name="Subquery Scan",
            )
            if subquery_plan:
                root.add_child(subquery_plan)
            current = root
        else:
            root = PlanNode(type=PlanNodeType.UNKNOWN, name="Unknown Source")
            current = root
    elif joins:
        # No FROM clause but has JOINs - first JOIN's left side is the main table
        # We need to infer the left table from the first JOIN condition
        first_join = joins[0]
        # For now, create a generic scan node for the implied table
        # In practice, the left table would be inferred from the JOIN condition
        root = PlanNode(type=PlanNodeType.SEQ_SCAN, name="Implied Table Scan")
        current = root
    else:
        # No FROM and no JOINs
        return PlanNode(
            type=PlanNodeType.RESULT,
            name="Result",
        )

    # Handle JOINs
    joins = select.args.get("joins", [])
    for join in joins:
        join_node = build_join_node(join, dialect)
        # Right side of join
        right_table = join.this
        if isinstance(right_table, exp.Table):
            right_node = build_scan_node(right_table, dialect)
        elif isinstance(right_table, exp.Subquery):
            right_plan = build_plan_from_select(right_table.this, dialect)
            right_node = PlanNode(type=PlanNodeType.SUBQUERY_SCAN, name="Subquery Scan")
            if right_plan:
                right_node.add_child(right_plan)
        else:
            right_node = PlanNode(type=PlanNodeType.UNKNOWN, name="Join Source")

        join_node.add_child(current)
        join_node.add_child(right_node)
        current = join_node
        root = join_node

    # Handle WHERE (filter on current node)
    where = select.args.get("where")
    if where and current:
        current.details["filter"] = where.this.sql(dialect=dialect)

    # Handle GROUP BY / Aggregates
    if select.args.get("group"):
        agg_node = build_aggregate_node(select, dialect)
        agg_node.add_child(current)
        current = agg_node
        root = agg_node

    # Handle HAVING
    having = select.args.get("having")
    if having and current:
        current.details["having"] = having.this.sql(dialect=dialect)

    # Handle ORDER BY
    if select.args.get("order"):
        sort_node = build_sort_node(select, dialect)
        sort_node.add_child(current)
        current = sort_node
        root = sort_node

    # Handle LIMIT
    if select.args.get("limit"):
        limit_node = build_limit_node(select, dialect)
        limit_node.add_child(current)
        root = limit_node

    return root


def build_plan(parsed: ParsedQuery, dialect: str) -> PlanNode:
    """Build execution plan from parsed query.

    Args:
        parsed: ParsedQuery from parser
        dialect: SQL dialect

    Returns:
        Root PlanNode of the execution plan

    Raises:
        PlanError: If plan cannot be built
    """
    try:
        ast = parsed.ast

        # Handle different statement types
        if isinstance(ast, exp.Select):
            plan = build_plan_from_select(ast, dialect)
            if not plan:
                plan = PlanNode(type=PlanNodeType.RESULT, name="Result")
            return plan

        elif isinstance(ast, exp.Insert):
            return PlanNode(
                type=PlanNodeType.INSERT,
                name="Insert",
                details={
                    "table": ast.this.name if ast.this else "unknown",
                },
            )

        elif isinstance(ast, exp.Update):
            return PlanNode(
                type=PlanNodeType.UPDATE,
                name="Update",
                details={
                    "table": ast.this.name if ast.this else "unknown",
                },
            )

        elif isinstance(ast, exp.Delete):
            return PlanNode(
                type=PlanNodeType.DELETE,
                name="Delete",
                details={
                    "table": ast.this.name if ast.this else "unknown",
                },
            )

        elif isinstance(ast, exp.Create):
            return PlanNode(
                type=PlanNodeType.UNKNOWN,
                name="Create",
                details={"kind": ast.kind},
            )

        else:
            return PlanNode(
                type=PlanNodeType.UNKNOWN,
                name=f"Unknown ({type(ast).__name__})",
            )

    except Exception as e:
        raise PlanError(f"Failed to build plan: {e}")


def walk_plan(root: PlanNode, fn, *args, **kwargs):
    """Walk plan tree and apply function to each node."""
    fn(root, *args, **kwargs)
    for child in root.children:
        walk_plan(child, fn, *args, **kwargs)


def plan_to_string(root: PlanNode, indent: int = 0) -> str:
    """Convert plan to readable string."""
    lines = []
    prefix = "  " * indent
    details_str = ""
    if root.details:
        details_str = " " + " ".join(f"{k}={v}" for k, v in root.details.items())
    lines.append(f"{prefix}{root.type.value}: {root.name}{details_str}")
    for child in root.children:
        lines.append(plan_to_string(child, indent + 1))
    return "\n".join(lines)