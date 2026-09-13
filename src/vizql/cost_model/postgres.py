"""PostgreSQL cost model."""

from .base import BaseCostModel, CostEstimate
from vizql.planner import PlanNode, PlanNodeType


class PostgresCostModel(BaseCostModel):
    """PostgreSQL-specific cost model with default constants."""

    # PostgreSQL default cost parameters
    SEQ_PAGE_COST = 1.0
    RANDOM_PAGE_COST = 4.0
    CPU_TUPLE_COST = 0.01
    CPU_INDEX_TUPLE_COST = 0.005
    CPU_OPERATOR_COST = 0.0025
    PARALLEL_TUPLE_COST = 0.1
    PARALLEL_SETUP_COST = 1000.0
    MIN_PARALLEL_TABLE_SCAN_SIZE = 8 * 1024 * 1024
    MIN_PARALLEL_INDEX_SCAN_SIZE = 512 * 1024
    EFFECTIVE_CACHE_SIZE = 4 * 1024 * 1024 * 1024
    WORK_MEM = 4 * 1024 * 1024
    MAINTENANCE_WORK_MEM = 64 * 1024 * 1024

    def estimate(self, node: PlanNode, table_stats: dict | None = None) -> CostEstimate:
        """Estimate cost for a plan node."""
        node_type = node.type

        if node_type == PlanNodeType.SEQ_SCAN:
            return self._estimate_seq_scan(node, table_stats)

        elif node_type == PlanNodeType.INDEX_SCAN:
            return self._estimate_index_scan(node, table_stats)

        elif node_type == PlanNodeType.HASH_JOIN:
            return self._estimate_hash_join(node, table_stats)

        elif node_type == PlanNodeType.NESTED_LOOP_JOIN:
            return self._estimate_nested_loop_join_detailed(node, table_stats)

        elif node_type == PlanNodeType.MERGE_JOIN:
            return self._estimate_merge_join(node, table_stats)

        elif node_type in (PlanNodeType.HASH_AGGREGATE, PlanNodeType.GROUP_AGGREGATE):
            return self._estimate_hash_aggregate_detailed(node, table_stats)

        elif node_type == PlanNodeType.SORT_AGGREGATE:
            return self._estimate_sort_aggregate(node, table_stats)

        elif node_type == PlanNodeType.SORT:
            return self._estimate_sort_detailed(node, table_stats)

        elif node_type == PlanNodeType.LIMIT:
            return self._estimate_limit_detailed(node, table_stats)

        elif node_type == PlanNodeType.CTE_SCAN:
            # CTE scan cost = child cost
            if node.children:
                return self.estimate(node.children[0], table_stats)
            return CostEstimate(rows=0, cost=0)

        elif node_type == PlanNodeType.SUBQUERY_SCAN:
            if node.children:
                return self.estimate(node.children[0], table_stats)
            return CostEstimate(rows=0, cost=0)

        elif node_type == PlanNodeType.MATERIALIZE:
            if node.children:
                return self.estimate(node.children[0], table_stats)
            return CostEstimate(rows=0, cost=0)

        else:
            # Unknown node type
            return CostEstimate(rows=1000, cost=100)

    def _estimate_hash_join(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate hash join with PostgreSQL specifics."""
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 1000
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 1000

        build_rows = min(left_rows, right_rows)
        probe_rows = max(left_rows, right_rows)

        # Hash table build
        hash_cpu = build_rows * (self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST * 2)
        hash_mem = build_rows * 200  # bytes per row with overhead

        # Probe
        probe_cpu = probe_rows * (self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST)

        # Join selectivity (default 0.1)
        output_rows = probe_rows * 0.1

        # Check if hash table fits in work_mem
        if hash_mem > self.WORK_MEM:
            # Spill to disk - batch processing
            batches = max(1, int(hash_mem / self.WORK_MEM))
            io_cost = batches * build_rows * self.SEQ_PAGE_COST / 100
        else:
            io_cost = 0

        cost = hash_cpu + probe_cpu + io_cost

        return CostEstimate(
            rows=max(1, output_rows),
            cost=cost,
            memory_mb=hash_mem / (1024 * 1024),
            cpu_cost=hash_cpu + probe_cpu,
            io_cost=io_cost,
        )

    def _estimate_nested_loop_join_detailed(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate nested loop join."""
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 1000
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 1000

        # For each outer row, scan inner (or use index)
        # Assume inner has index for indexed NLJ
        outer_rows = left_rows
        inner_rows = right_rows

        # CPU cost per outer row
        cpu_per_row = self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST
        cost = outer_rows * (cpu_per_row + inner_rows * 0.01)  # 1% of inner scanned per outer

        output_rows = outer_rows * 0.1

        return CostEstimate(
            rows=max(1, output_rows),
            cost=cost,
            cpu_cost=cost,
        )

    def _estimate_merge_join(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate merge join."""
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 1000
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 1000

        # Merge join requires sorted inputs
        # Sort cost + merge cost
        sort_cost = self._estimate_sort_cost(left_rows) + self._estimate_sort_cost(right_rows)
        merge_cost = (left_rows + right_rows) * self.CPU_OPERATOR_COST

        output_rows = min(left_rows, right_rows) * 0.5

        return CostEstimate(
            rows=max(1, output_rows),
            cost=sort_cost + merge_cost,
            cpu_cost=merge_cost,
        )

    def _estimate_sort_cost(self, rows: float) -> float:
        """Estimate sort cost for merge join input."""
        import math
        if rows <= 1:
            return 0
        comparisons = rows * math.log2(rows)
        return comparisons * self.CPU_OPERATOR_COST

    def _estimate_hash_aggregate_detailed(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate hash aggregate."""
        input_rows = node.children[0].est_rows if node.children else 1000
        group_cols = len(node.details.get("group_by", []))

        # Hash aggregate builds hash table
        build_cpu = input_rows * (self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST * 2)
        hash_mem = input_rows * 150  # bytes per row

        # Output = distinct groups
        output_rows = max(1, input_rows / max(1, group_cols * 20))

        # Check work_mem
        if hash_mem > self.WORK_MEM:
            io_cost = (hash_mem / self.WORK_MEM) * input_rows * self.SEQ_PAGE_COST / 100
        else:
            io_cost = 0

        cost = build_cpu + io_cost

        return CostEstimate(
            rows=output_rows,
            cost=cost,
            memory_mb=hash_mem / (1024 * 1024),
            cpu_cost=build_cpu,
            io_cost=io_cost,
        )

    def _estimate_sort_aggregate(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate sort aggregate (requires sorted input)."""
        input_rows = node.children[0].est_rows if node.children else 1000
        group_cols = len(node.details.get("group_by", []))

        # Sort + aggregate
        sort_cost = self._estimate_sort_cost(input_rows)
        agg_cost = input_rows * self.CPU_OPERATOR_COST

        output_rows = max(1, input_rows / max(1, group_cols * 20))

        return CostEstimate(
            rows=output_rows,
            cost=sort_cost + agg_cost,
            cpu_cost=sort_cost + agg_cost,
        )

    def _estimate_sort_detailed(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate sort node."""
        input_rows = node.children[0].est_rows if node.children else 1000
        sort_key_size = 100

        import math
        if input_rows <= 1:
            return CostEstimate(rows=input_rows, cost=0)

        comparisons = input_rows * math.log2(input_rows)
        cpu_cost = comparisons * self.CPU_OPERATOR_COST
        memory = input_rows * sort_key_size

        if memory > self.WORK_MEM:
            io_cost = (memory / self.WORK_MEM) * input_rows * self.SEQ_PAGE_COST / 100
        else:
            io_cost = 0

        cost = cpu_cost + io_cost

        return CostEstimate(
            rows=input_rows,
            cost=cost,
            memory_mb=memory / (1024 * 1024),
            cpu_cost=cpu_cost,
            io_cost=io_cost,
        )

    def _estimate_limit_detailed(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate limit node."""
        input_rows = node.children[0].est_rows if node.children else 1000
        limit_val = int(node.details.get("limit", 100))

        output_rows = min(input_rows, limit_val)
        cost = output_rows * self.CPU_TUPLE_COST

        return CostEstimate(
            rows=output_rows,
            cost=cost,
            cpu_cost=cost,
        )