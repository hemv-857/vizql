"""DuckDB cost model."""

from .base import BaseCostModel, CostEstimate
from vizql.planner import PlanNode, PlanNodeType


class DuckDBCostModel(BaseCostModel):
    """DuckDB-specific cost model.

    DuckDB is an embedded analytical database:
    - Vectorized execution
    - Columnar storage
    - No network cost (embedded)
    - morsel-driven parallelism
    """

    def estimate(self, node: PlanNode, table_stats: dict | None = None) -> CostEstimate:
        node_type = node.type

        if node_type == PlanNodeType.SEQ_SCAN:
            return self._estimate_table_scan(node, table_stats)

        elif node_type == PlanNodeType.HASH_JOIN:
            return self._estimate_join(node, table_stats)

        elif node_type in (PlanNodeType.HASH_AGGREGATE, PlanNodeType.GROUP_AGGREGATE):
            return self._estimate_aggregate(node, table_stats)

        elif node_type == PlanNodeType.SORT:
            return self._estimate_sort(node, table_stats)

        elif node_type == PlanNodeType.LIMIT:
            return self._estimate_limit(node, table_stats)

        else:
            return CostEstimate(rows=1000, cost=100)

    def _estimate_table_scan(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        rows = self._get_table_rows(node, table_stats)
        # DuckDB columnar scan is very fast
        cost = rows * 0.00001  # microseconds per row

        return CostEstimate(
            rows=rows,
            cost=cost * 1_000_000,
            cpu_cost=cost * 1_000_000,
        )

    def _estimate_join(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 1000
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 1000

        build_rows = min(left_rows, right_rows)
        probe_rows = max(left_rows, right_rows)

        # DuckDB hash join is very efficient
        cost = (build_rows + probe_rows) * 0.000005
        output_rows = probe_rows * 0.1

        return CostEstimate(
            rows=max(1, output_rows),
            cost=cost * 1_000_000,
            cpu_cost=cost * 1_000_000,
        )

    def _estimate_aggregate(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        input_rows = node.children[0].est_rows if node.children else 1000
        group_cols = len(node.details.get("group_by", []))

        # Vectorized aggregation
        cost = input_rows * 0.000002
        output_rows = max(1, input_rows / max(1, group_cols * 20))

        return CostEstimate(
            rows=output_rows,
            cost=cost * 1_000_000,
            cpu_cost=cost * 1_000_000,
        )

    def _estimate_sort(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        input_rows = node.children[0].est_rows if node.children else 1000
        import math
        if input_rows <= 1:
            return CostEstimate(rows=input_rows, cost=0)

        # Radix sort for integers, merge sort for others
        cost = input_rows * math.log2(input_rows) * 0.000001
        return CostEstimate(rows=input_rows, cost=cost * 1_000_000, cpu_cost=cost * 1_000_000)

    def _estimate_limit(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        input_rows = node.children[0].est_rows if node.children else 1000
        limit_val = int(node.details.get("limit", 100))
        output_rows = min(input_rows, limit_val)
        return CostEstimate(rows=output_rows, cost=output_rows * 0.000001 * 1_000_000)

    def _get_table_rows(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> float:
        table_name = node.details.get("table", "")
        if table_stats and table_name in table_stats:
            return float(table_stats[table_name].get("rows", 1_000_000))
        return 1_000_000