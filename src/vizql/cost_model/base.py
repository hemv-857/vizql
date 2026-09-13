"""Base cost model for query execution estimation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from vizql.planner import PlanNode, PlanNodeType


@dataclass
class CostEstimate:
    """Cost estimate for a plan node."""
    rows: float
    cost: float  # Planner cost units
    memory_mb: float = 0
    cpu_cost: float = 0
    io_cost: float = 0


class BaseCostModel(ABC):
    """Base class for dialect-specific cost models."""

    # Cost constants (PostgreSQL defaults)
    SEQ_PAGE_COST = 1.0
    RANDOM_PAGE_COST = 4.0
    CPU_TUPLE_COST = 0.01
    CPU_INDEX_TUPLE_COST = 0.005
    CPU_OPERATOR_COST = 0.0025
    PARALLEL_TUPLE_COST = 0.1
    PARALLEL_SETUP_COST = 1000.0
    MIN_PARALLEL_TABLE_SCAN_SIZE = 8 * 1024 * 1024  # 8MB
    MIN_PARALLEL_INDEX_SCAN_SIZE = 512 * 1024  # 512KB
    EFFECTIVE_CACHE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
    WORK_MEM = 4 * 1024 * 1024  # 4MB
    MAINTENANCE_WORK_MEM = 64 * 1024 * 1024  # 64MB

    @abstractmethod
    def estimate(self, node: PlanNode, table_stats: dict[str, Any] | None = None) -> CostEstimate:
        """Estimate cost for a plan node."""
        pass

    def _estimate_seq_scan(
        self,
        node: PlanNode,
        table_stats: dict[str, Any] | None = None,
    ) -> CostEstimate:
        """Estimate sequential scan cost."""
        rows = self._get_table_rows(node, table_stats)
        pages = max(1, rows / 100)  # Assume 100 rows per page

        io_cost = pages * self.SEQ_PAGE_COST
        cpu_cost = rows * self.CPU_TUPLE_COST
        cost = io_cost + cpu_cost

        return CostEstimate(
            rows=rows,
            cost=cost,
            io_cost=io_cost,
            cpu_cost=cpu_cost,
        )

    def _estimate_index_scan(
        self,
        node: PlanNode,
        table_stats: dict[str, Any] | None = None,
        selectivity: float = 0.1,
    ) -> CostEstimate:
        """Estimate index scan cost."""
        total_rows = self._get_table_rows(node, table_stats)
        rows = max(1, total_rows * selectivity)
        pages = max(1, rows / 100)

        io_cost = pages * self.RANDOM_PAGE_COST
        cpu_cost = rows * self.CPU_INDEX_TUPLE_COST
        cost = io_cost + cpu_cost

        return CostEstimate(
            rows=rows,
            cost=cost,
            io_cost=io_cost,
            cpu_cost=cpu_cost,
        )

    def _estimate_hash_join(
        self,
        node: PlanNode,
        left_rows: float,
        right_rows: float,
        table_stats: dict[str, Any] | None = None,
    ) -> CostEstimate:
        """Estimate hash join cost."""
        # Build side (smaller) + probe side (larger)
        build_rows = min(left_rows, right_rows)
        probe_rows = max(left_rows, right_rows)

        # Hash table build cost
        build_cost = build_rows * (self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST)
        build_memory = build_rows * 100  # bytes per row estimate

        # Probe cost
        probe_cost = probe_rows * (self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST)

        # Output rows (estimate based on join selectivity)
        output_rows = probe_rows * 0.1  # Default 10% selectivity

        cost = build_cost + probe_cost

        return CostEstimate(
            rows=output_rows,
            cost=cost,
            memory_mb=build_memory / (1024 * 1024),
            cpu_cost=build_cost + probe_cost,
        )

    def _estimate_nested_loop_join(
        self,
        node: PlanNode,
        left_rows: float,
        right_rows: float,
        table_stats: dict[str, Any] | None = None,
    ) -> CostEstimate:
        """Estimate nested loop join cost."""
        outer_rows = left_rows
        inner_rows = right_rows

        # For each outer row, scan inner
        cost = outer_rows * (inner_rows * self.CPU_TUPLE_COST)
        output_rows = outer_rows * inner_rows * 0.01  # 1% selectivity default

        return CostEstimate(
            rows=output_rows,
            cost=cost,
            cpu_cost=cost,
        )

    def _estimate_hash_aggregate(
        self,
        node: PlanNode,
        input_rows: float,
        group_cols: int = 1,
    ) -> CostEstimate:
        """Estimate hash aggregate cost."""
        # Hash aggregate: build hash table, then aggregate
        # Output rows = number of distinct groups
        output_rows = max(1, input_rows / max(1, group_cols * 10))

        build_cost = input_rows * (self.CPU_TUPLE_COST + self.CPU_OPERATOR_COST)
        memory = input_rows * 50  # bytes per row

        cost = build_cost

        return CostEstimate(
            rows=output_rows,
            cost=cost,
            memory_mb=memory / (1024 * 1024),
            cpu_cost=build_cost,
        )

    def _estimate_sort(
        self,
        node: PlanNode,
        input_rows: float,
        sort_key_size: int = 100,
    ) -> CostEstimate:
        """Estimate sort cost."""
        # External sort cost: N * log2(N) comparisons
        import math
        if input_rows <= 1:
            return CostEstimate(rows=input_rows, cost=0)

        comparisons = input_rows * math.log2(input_rows)
        cpu_cost = comparisons * self.CPU_OPERATOR_COST

        # Memory for sort
        memory = input_rows * sort_key_size

        # Check if spill to disk
        if memory > self.WORK_MEM:
            # Spill cost
            io_cost = (memory / self.WORK_MEM) * input_rows * self.SEQ_PAGE_COST
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

    def _estimate_limit(
        self,
        node: PlanNode,
        input_rows: float,
        limit: int,
    ) -> CostEstimate:
        """Estimate limit cost."""
        output_rows = min(input_rows, limit)
        # Limit just passes through, small CPU cost
        cost = output_rows * self.CPU_TUPLE_COST
        return CostEstimate(rows=output_rows, cost=cost, cpu_cost=cost)

    def _get_table_rows(
        self,
        node: PlanNode,
        table_stats: dict[str, Any] | None = None,
    ) -> float:
        """Get estimated row count for table."""
        table_name = node.details.get("table", "")
        if table_stats and table_name in table_stats:
            return float(table_stats[table_name].get("rows", 1000))
        # Default: assume 1000 rows
        return 1000.0


def get_cost_model(dialect: str) -> BaseCostModel:
    """Get cost model for dialect."""
    from .postgres import PostgresCostModel
    from .snowflake import SnowflakeCostModel
    from .bigquery import BigQueryCostModel
    from .duckdb import DuckDBCostModel

    dialect = dialect.lower()
    if dialect in ("postgres", "postgresql", "pg"):
        return PostgresCostModel()
    elif dialect == "snowflake":
        return SnowflakeCostModel()
    elif dialect == "bigquery":
        return BigQueryCostModel()
    elif dialect == "duckdb":
        return DuckDBCostModel()
    else:
        # Default to Postgres
        return PostgresCostModel()