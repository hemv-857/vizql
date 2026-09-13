"""Snowflake cost model."""

from .base import BaseCostModel, CostEstimate
from vizql.planner import PlanNode, PlanNodeType


class SnowflakeCostModel(BaseCostModel):
    """Snowflake-specific cost model.

    Snowflake uses a different architecture:
    - Columnar storage with micro-partitions
    - Pruning via metadata
    - No traditional indexes (uses clustering keys)
    - Warehouse-based compute (credits per second)
    """

    # Snowflake-specific constants
    # Based on X-Small warehouse (1 credit/hour)
    CREDITS_PER_HOUR = 1.0
    BYTES_SCANNED_PER_CREDIT = 1_000_000_000_000  # ~1TB per credit (rough)
    MICRO_PARTITION_SIZE = 16 * 1024 * 1024  # 16MB compressed

    def estimate(self, node: PlanNode, table_stats: dict | None = None) -> CostEstimate:
        """Estimate cost for a Snowflake plan node."""
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
        """Estimate Snowflake table scan (micro-partition scan)."""
        rows = self._get_table_rows(node, table_stats)
        bytes_per_row = 200  # Average row size after compression
        bytes_scanned = rows * bytes_per_row

        # Pruning estimation
        pruning_factor = 1.0
        if "filter" in node.details:
            # Rough estimate: filter might prune 50-90%
            pruning_factor = 0.3
            bytes_scanned *= pruning_factor

        # Cost in credits
        credits = bytes_scanned / self.BYTES_SCANNED_PER_CREDIT

        return CostEstimate(
            rows=rows * pruning_factor,
            cost=credits * 10000,  # Scale for display
            memory_mb=bytes_scanned / (1024 * 1024),
            cpu_cost=credits * 10000,
            io_cost=0,
        )

    def _estimate_join(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate Snowflake join (hash join with broadcast)."""
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 1000
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 1000

        # Snowflake broadcasts smaller table
        build_rows = min(left_rows, right_rows)
        probe_rows = max(left_rows, right_rows)

        # Broadcast cost + hash join
        broadcast_cost = build_rows * 0.001  # Network
        hash_cost = (build_rows + probe_rows) * 0.0001

        output_rows = probe_rows * 0.1

        cost = broadcast_cost + hash_cost

        return CostEstimate(
            rows=max(1, output_rows),
            cost=cost * 10000,
            cpu_cost=(hash_cost + broadcast_cost) * 10000,
        )

    def _estimate_aggregate(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate Snowflake aggregate."""
        input_rows = node.children[0].est_rows if node.children else 1000
        group_cols = len(node.details.get("group_by", []))

        # Hash aggregate with partition pruning
        cost = input_rows * 0.00005
        output_rows = max(1, input_rows / max(1, group_cols * 50))

        return CostEstimate(
            rows=output_rows,
            cost=cost * 10000,
            cpu_cost=cost * 10000,
        )

    def _estimate_sort(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate Snowflake sort (uses spill to remote storage)."""
        input_rows = node.children[0].est_rows if node.children else 1000
        import math

        if input_rows <= 1:
            return CostEstimate(rows=input_rows, cost=0)

        # Snowflake sorts in memory, spills to remote storage
        cost = input_rows * math.log2(input_rows) * 0.00001
        memory = input_rows * 100

        return CostEstimate(
            rows=input_rows,
            cost=cost * 10000,
            memory_mb=memory / (1024 * 1024),
        )

    def _estimate_limit(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        input_rows = node.children[0].est_rows if node.children else 1000
        limit_val = int(node.details.get("limit", 100))
        output_rows = min(input_rows, limit_val)
        return CostEstimate(rows=output_rows, cost=output_rows * 0.00001 * 10000)

    def _get_table_rows(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> float:
        table_name = node.details.get("table", "")
        if table_stats and table_name in table_stats:
            return float(table_stats[table_name].get("rows", 1_000_000))
        return 1_000_000  # Snowflake tables tend to be larger