"""BigQuery cost model."""

from .base import BaseCostModel, CostEstimate
from vizql.planner import PlanNode, PlanNodeType


class BigQueryCostModel(BaseCostModel):
    """BigQuery-specific cost model.

    BigQuery pricing:
    - On-demand: $5.00 per TB processed
    - Slot-based: reserved slots
    - Columnar storage with partition/cluster pruning
    """

    # BigQuery pricing (on-demand)
    TB_PROCESSED_COST = 5.0  # $5/TB
    SLOT_COST_PER_HOUR = 0.04  # $0.04/slot/hour (flex slots)

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
        """Estimate BigQuery table scan with partition/cluster pruning."""
        rows = self._get_table_rows(node, table_stats)
        bytes_per_row = 150  # Average compressed columnar
        bytes_scanned = rows * bytes_per_row

        # Partition pruning
        pruning_factor = 1.0
        if "filter" in node.details:
            # BigQuery can prune partitions effectively
            pruning_factor = 0.1  # Up to 90% pruning
            bytes_scanned *= pruning_factor

        # Clustering pruning
        if node.details.get("clustered"):
            bytes_scanned *= 0.5

        cost_usd = (bytes_scanned / (1024**4)) * self.TB_PROCESSED_COST

        return CostEstimate(
            rows=rows * pruning_factor,
            cost=cost_usd * 10000,  # Scale for display
            memory_mb=bytes_scanned / (1024 * 1024),
            cpu_cost=cost_usd * 10000,
        )

    def _estimate_join(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        """Estimate BigQuery join (broadcast hash join)."""
        left_rows = node.children[0].est_rows if len(node.children) > 0 else 1000
        right_rows = node.children[1].est_rows if len(node.children) > 1 else 1000

        # BigQuery uses broadcast join for small tables
        # Or shuffle/hash join for large
        build_rows = min(left_rows, right_rows)
        probe_rows = max(left_rows, right_rows)

        # Cost proportional to data shuffled
        shuffle_bytes = (build_rows + probe_rows) * 150
        cost_usd = (shuffle_bytes / (1024**4)) * self.TB_PROCESSED_COST

        output_rows = probe_rows * 0.1

        return CostEstimate(
            rows=max(1, output_rows),
            cost=cost_usd * 10000,
            cpu_cost=cost_usd * 10000,
        )

    def _estimate_aggregate(
        self,
        node: PlanNode,
        table_stats: dict | None = None,
    ) -> CostEstimate:
        input_rows = node.children[0].est_rows if node.children else 1000
        group_cols = len(node.details.get("group_by", []))

        # Distributed aggregation
        cost_usd = input_rows * 150 / (1024**4) * self.TB_PROCESSED_COST
        output_rows = max(1, input_rows / max(1, group_cols * 100))

        return CostEstimate(
            rows=output_rows,
            cost=cost_usd * 10000,
            cpu_cost=cost_usd * 10000,
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

        # BigQuery sorts using slots
        slot_ms = input_rows * math.log2(input_rows) * 0.001
        cost_usd = (slot_ms / 3_600_000) * self.SLOT_COST_PER_HOUR

        return CostEstimate(
            rows=input_rows,
            cost=cost_usd * 10000,
            cpu_cost=cost_usd * 10000,
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
            return float(table_stats[table_name].get("rows", 10_000_000))
        return 10_000_000  # BigQuery tables are often large