"""Plan diff utilities."""

from dataclasses import dataclass
from typing import Any
from vizql.parser import parse_sql
from vizql.planner import build_plan
from vizql.simulator import run_full_simulation


class DiffError(Exception):
    """Diff error."""
    pass


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
        delta = self.new_cost - self.old_cost
        ratio = self.new_cost / self.old_cost if self.old_cost > 0 else 0
        lines.append(f"Delta:    {delta:,.0f} ({ratio:.1f}x)")
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


def diff_plans(
    old_sql: str,
    new_sql: str,
    dialect: str = "postgres",
) -> PlanDiff:
    """Compare two SQL queries."""
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