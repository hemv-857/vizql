"""Regression detection for SQL queries."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import subprocess
import re

from .parser import parse_sql, ParseError
from .planner import build_plan, PlanError
from .simulator import run_full_simulation, SimulationResult
from .cost_model import get_cost_model


@dataclass
class Regression:
    """A detected SQL regression."""
    file: str
    old_cost: float
    new_cost: float
    ratio: float
    warnings: list[str] = field(default_factory=list)
    suggestion: str = ""


@dataclass
class PassedQuery:
    """A query with no regression."""
    file: str
    cost: float


@dataclass
class RegressionResult:
    """Result of regression check."""
    regressions: list[Regression] = field(default_factory=list)
    passed: list[PassedQuery] = field(default_factory=list)


class RegressionError(Exception):
    """Regression check error."""
    pass


def check_regressions(
    compare_branch: str = "main",
    dialect: str = "postgres",
    database_url: str | None = None,
    threshold: float = 1.5,
    path: Path = Path("."),
) -> RegressionResult:
    """Check for SQL regressions between branches.

    Args:
        compare_branch: Git branch to compare against
        dialect: SQL dialect
        database_url: Database connection URL (for EXPLAIN)
        threshold: Cost ratio threshold for regression (1.5 = 50% increase)
        path: Path to search for SQL files

    Returns:
        RegressionResult with regressions and passed queries
    """
    result = RegressionResult()

    # Find changed SQL files
    changed_files = _get_changed_sql_files(compare_branch, path)
    if not changed_files:
        return result

    cost_model = get_cost_model(dialect)

    for sql_file in changed_files:
        try:
            sql_text = sql_file.read_text()
            if not sql_text.strip():
                continue

            # Parse and plan
            parsed = parse_sql(sql_text, dialect)
            plan = build_plan(parsed, dialect)

            # Estimate costs
            sim_result = run_full_simulation(plan, dialect)
            new_cost = sim_result.total_cost

            # Get old cost from base branch
            old_cost = _get_base_branch_cost(sql_file, compare_branch, dialect, cost_model)

            if old_cost is None:
                # New query, no baseline
                result.passed.append(PassedQuery(file=str(sql_file), cost=new_cost))
                continue

            ratio = new_cost / old_cost if old_cost > 0 else 1.0

            if ratio >= threshold:
                # Regression detected
                warnings = []
                if ratio > 10:
                    warnings.append(f"Critical: {ratio:.1f}x cost increase")
                elif ratio > 3:
                    warnings.append(f"Major: {ratio:.1f}x cost increase")
                else:
                    warnings.append(f"Moderate: {ratio:.1f}x cost increase")

                # Add plan-specific warnings
                warnings.extend(sim_result.warnings)

                suggestion = _generate_suggestion(plan, ratio)
                result.regressions.append(Regression(
                    file=str(sql_file),
                    old_cost=old_cost,
                    new_cost=new_cost,
                    ratio=ratio,
                    warnings=warnings,
                    suggestion=suggestion,
                ))
            else:
                result.passed.append(PassedQuery(file=str(sql_file), cost=new_cost))

        except (ParseError, PlanError) as e:
            # Skip files that can't be parsed
            continue
        except Exception:
            # Skip other errors
            continue

    return result


def _get_changed_sql_files(compare_branch: str, path: Path) -> list[Path]:
    """Get SQL files changed compared to base branch."""
    try:
        # Get diff of SQL files
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{compare_branch}...HEAD"],
            capture_output=True,
            text=True,
            cwd=path,
        )
        if result.returncode != 0:
            return []

        files = []
        for line in result.stdout.strip().split("\n"):
            if line.endswith(".sql"):
                f = path / line
                if f.exists():
                    files.append(f)
        return files
    except Exception:
        return []


def _get_base_branch_cost(
    sql_file: Path,
    compare_branch: str,
    dialect: str,
    cost_model,
) -> float | None:
    """Get cost of query on base branch."""
    try:
        # Get file content at base branch
        result = subprocess.run(
            ["git", "show", f"{compare_branch}:{sql_file}"],
            capture_output=True,
            text=True,
            cwd=sql_file.parent,
        )
        if result.returncode != 0:
            return None  # New file

        old_sql = result.stdout
        if not old_sql.strip():
            return None

        # Parse and estimate
        parsed = parse_sql(old_sql, dialect)
        plan = build_plan(parsed, dialect)
        sim_result = run_full_simulation(plan, dialect)
        return sim_result.total_cost

    except Exception:
        return None


def _generate_suggestion(plan, ratio: float) -> str:
    """Generate fix suggestion based on plan."""
    # Walk plan for common patterns
    suggestions = []

    def walk(node):
        if node.type.name == "SEQ_SCAN" and node.est_rows > 10000:
            table = node.details.get("table", "table")
            suggestions.append(f"Add index on {table}")
        elif node.type.name == "HASH_JOIN":
            mem = node.details.get("est_memory_mb", 0)
            if mem > 100:
                suggestions.append("Increase work_mem")
        elif node.type.name == "NESTED_LOOP_JOIN":
            suggestions.append("Add index on join column")

        for child in node.children:
            walk(child)

    walk(plan)

    if suggestions:
        return "; ".join(suggestions[:2])
    return "Analyze query plan for optimization opportunities"


def diff_plans(
    old_sql: str,
    new_sql: str,
    dialect: str = "postgres",
) -> "PlanDiff":
    """Compare two SQL queries."""
    from .parser import parse_sql
    from .planner import build_plan
    from .simulator import run_full_simulation

    old_parsed = parse_sql(old_sql, dialect)
    new_parsed = parse_sql(new_sql, dialect)

    old_plan = build_plan(old_parsed, dialect)
    new_plan = build_plan(new_parsed, dialect)

    old_sim = run_full_simulation(old_plan, dialect)
    new_sim = run_full_simulation(new_plan, dialect)

    return PlanDiff(
        old_plan=old_plan,
        new_plan=new_plan,
        old_cost=old_sim.total_cost,
        new_cost=new_sim.total_cost,
        old_warnings=old_sim.warnings,
        new_warnings=new_sim.warnings,
    )


@dataclass
class PlanDiff:
    """Difference between two plans."""
    old_plan: Any
    new_plan: Any
    old_cost: float
    new_cost: float
    old_warnings: list[str]
    new_warnings: list[str]

    def render(self) -> str:
        """Render as text comparison."""
        lines = []
        lines.append("=" * 60)
        lines.append("PLAN COMPARISON")
        lines.append("=" * 60)
        lines.append(f"Old Cost: {self.old_cost:,.0f}")
        lines.append(f"New Cost: {self.new_cost:,.0f}")
        lines.append(f"Delta:    {self.new_cost - self.old_cost:,.0f} ({self.new_cost/self.old_cost:.1f}x)")
        lines.append("")

        if self.old_warnings:
            lines.append("OLD WARNINGS:")
            for w in self.old_warnings:
                lines.append(f"  ⚠️  {w}")
            lines.append("")

        if self.new_warnings:
            lines.append("NEW WARNINGS:")
            for w in self.new_warnings:
                lines.append(f"  ⚠️  {w}")
            lines.append("")

        lines.append("OLD PLAN:")
        lines.append(self._plan_to_str(self.old_plan))
        lines.append("")
        lines.append("NEW PLAN:")
        lines.append(self._plan_to_str(self.new_plan))

        return "\n".join(lines)

    def _plan_to_str(self, node, indent: int = 0) -> str:
        prefix = "  " * indent
        lines = [f"{prefix}{node.type.value}: {node.name} (Rows: {node.est_rows:,.0f}, Cost: {node.est_cost:,.0f})"]
        for child in node.children:
            lines.append(self._plan_to_str(child, indent + 1))
        return "\n".join(lines)