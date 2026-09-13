"""Tests for vizql."""

import pytest
from pathlib import Path

from vizql.parser import parse_sql, ParseError
from vizql.planner import build_plan, PlanNodeType
from vizql.simulator import simulate_execution, run_full_simulation
from vizql.cost_model import get_cost_model
from vizql.advisor import analyze_plan
from vizql.renderers.mermaid import render_mermaid
from vizql.renderers.terminal import render_terminal


SIMPLE_SQL = "SELECT * FROM users WHERE id = 1"
JOIN_SQL = """
SELECT u.name, o.total
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE u.status = 'active'
"""
AGG_SQL = """
SELECT user_id, COUNT(*) as cnt
FROM orders
GROUP BY user_id
HAVING COUNT(*) > 10
"""


class TestParser:
    """Tests for SQL parser."""

    def test_simple_select(self):
        parsed = parse_sql(SIMPLE_SQL, "postgres")
        assert parsed.dialect == "postgres"
        assert "users" in parsed.tables
        assert "id" in parsed.columns

    def test_join(self):
        parsed = parse_sql(JOIN_SQL, "postgres")
        assert "users" in parsed.tables
        assert "orders" in parsed.tables
        assert "user_id" in parsed.columns

    def test_aggregate(self):
        parsed = parse_sql(AGG_SQL, "postgres")
        assert "orders" in parsed.tables
        assert "user_id" in parsed.columns

    def test_dialect_normalization(self):
        parsed = parse_sql(SIMPLE_SQL, "PG")
        assert parsed.dialect == "postgres"

        parsed = parse_sql(SIMPLE_SQL, "MySQL")
        assert parsed.dialect == "mysql"

    def test_invalid_sql(self):
        with pytest.raises(ParseError):
            parse_sql("SELECT * FROM WHERE", "postgres")


class TestPlanner:
    """Tests for plan builder."""

    def test_simple_plan(self):
        parsed = parse_sql(SIMPLE_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        assert plan is not None
        # Should have a scan node
        assert plan.type in (PlanNodeType.SEQ_SCAN, PlanNodeType.INDEX_SCAN, PlanNodeType.RESULT)

    def test_join_plan(self):
        parsed = parse_sql(JOIN_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        assert plan is not None
        # Should have a join at root
        assert plan.type == PlanNodeType.HASH_JOIN

    def test_aggregate_plan(self):
        parsed = parse_sql(AGG_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        assert plan is not None
        # Should have aggregate at root
        assert plan.type in (PlanNodeType.HASH_AGGREGATE, PlanNodeType.GROUP_AGGREGATE)


class TestCostModel:
    """Tests for cost models."""

    def test_postgres_model(self):
        model = get_cost_model("postgres")
        assert model is not None

    def test_snowflake_model(self):
        model = get_cost_model("snowflake")
        assert model is not None

    def test_bigquery_model(self):
        model = get_cost_model("bigquery")
        assert model is not None

    def test_duckdb_model(self):
        model = get_cost_model("duckdb")
        assert model is not None


class TestSimulator:
    """Tests for execution simulator."""

    def test_simulate_simple(self):
        parsed = parse_sql(SIMPLE_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        frames = simulate_execution(plan, "postgres")
        assert len(frames) > 0

    def test_simulate_join(self):
        parsed = parse_sql(JOIN_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        frames = simulate_execution(plan, "postgres")
        assert len(frames) > 2

    def test_full_simulation(self):
        parsed = parse_sql(JOIN_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        result = run_full_simulation(plan, "postgres")
        assert result.total_cost > 0
        assert result.total_rows > 0


class TestAdvisor:
    """Tests for advisor."""

    def test_seq_scan_warning(self):
        parsed = parse_sql("SELECT * FROM users WHERE name = 'test'", "postgres")
        plan = build_plan(parsed, "postgres")
        result = analyze_plan(plan)
        # Should have index recommendation for filtered column
        assert len(result.index_recommendations) > 0
        assert result.index_recommendations[0]["table"] == "users"

    def test_hash_join_memory_warning(self):
        # Large hash join
        sql = """
        SELECT * FROM large_table_a a
        JOIN large_table_b b ON a.id = b.id
        """
        parsed = parse_sql(sql, "postgres")
        plan = build_plan(parsed, "postgres")
        result = analyze_plan(plan)
        # Check for warnings/suggestions


class TestRenderers:
    """Tests for renderers."""

    def test_mermaid_render(self):
        parsed = parse_sql(SIMPLE_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        mermaid = render_mermaid(plan)
        assert "flowchart TD" in mermaid
        assert "```mermaid" in mermaid

    def test_terminal_render(self):
        parsed = parse_sql(SIMPLE_SQL, "postgres")
        plan = build_plan(parsed, "postgres")
        result = render_terminal(plan)
        assert result is not None


class TestExamples:
    """Test example files parse correctly."""

    def test_nasty_join(self):
        sql = Path("examples/nasty_join.sql").read_text()
        parsed = parse_sql(sql, "postgres")
        plan = build_plan(parsed, "postgres")
        assert plan is not None

    def test_cte_hell(self):
        sql = Path("examples/cte_hell.sql").read_text()
        parsed = parse_sql(sql, "postgres")
        plan = build_plan(parsed, "postgres")
        assert plan is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])