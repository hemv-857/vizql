"""CLI entrypoint for vizql."""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from .parser import parse_sql, ParseError
from .planner import PlanNode, build_plan, PlanError
from .simulator import simulate_execution, SimulationError
from .renderers.mermaid import render_mermaid
from .renderers.graphviz import render_graphviz
from .renderers.terminal import render_terminal
from .advisor import analyze_plan, AdvisorResult

app = typer.Typer(
    name="vizql",
    help="Visual SQL Explainer - See your SQL run",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


@app.command()
def explain(
    sql: str = typer.Argument(None, help="SQL query to explain (or pass via stdin)"),
    dialect: str = typer.Option("postgres", "--dialect", "-d", help="SQL dialect"),
    format: str = typer.Option("terminal", "--format", "-f", help="Output format: terminal, mermaid, graphviz, json"),
    show_costs: bool = typer.Option(True, "--costs/--no-costs", help="Show cost estimates"),
    show_warnings: bool = typer.Option(True, "--warnings/--no-warnings", help="Show warnings"),
    show_suggestions: bool = typer.Option(True, "--suggestions/--no-suggestions", help="Show optimization suggestions"),
    file: Optional[Path] = typer.Option(None, "--file", help="Read SQL from file"),
    animate: bool = typer.Option(False, "--animate", help="Show animated execution (terminal only)"),
):
    """Explain a SQL query with visual execution plan."""
    # Read SQL from file, arg, or stdin
    if file:
        sql_text = file.read_text()
    elif sql:
        sql_text = sql
    else:
        if sys.stdin.isatty():
            console.print("[red]Error:[/red] No SQL provided. Pass as argument, --file, or stdin.")
            raise typer.Exit(1)
        sql_text = sys.stdin.read()

    if not sql_text.strip():
        console.print("[red]Error:[/red] Empty SQL query")
        raise typer.Exit(1)

    try:
        # Parse
        ast = parse_sql(sql_text, dialect)
        # Build plan
        plan = build_plan(ast, dialect)
        # Simulate execution
        frames = simulate_execution(plan)
        # Analyze for suggestions
        advisor = analyze_plan(plan, frames)

        # Render based on format
        if format == "mermaid":
            output = render_mermaid(plan, advisor if show_warnings else None)
            console.print(output, markup=False)
        elif format == "graphviz":
            output = render_graphviz(plan, advisor if show_warnings else None)
            console.print(output, markup=False)
        elif format == "json":
            import json
            output = json.dumps({
                "plan": plan.to_dict(),
                "frames": [f.to_dict() for f in frames],
                "advisor": advisor.to_dict() if show_warnings else None,
            }, indent=2)
            console.print(output, markup=False)
        else:  # terminal
            output = render_terminal(
                plan,
                frames,
                advisor if show_warnings else None,
                show_costs=show_costs,
                show_suggestions=show_suggestions,
                animate=animate,
            )
            console.print(output)

    except ParseError as e:
        console.print(f"[red]Parse Error:[/red] {e}")
        raise typer.Exit(1)
    except PlanError as e:
        console.print(f"[red]Plan Error:[/red] {e}")
        raise typer.Exit(1)
    except SimulationError as e:
        console.print(f"[red]Simulation Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected Error:[/red] {e}")
        if console.is_terminal:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def check(
    compare_branch: str = typer.Option("main", "--compare", "-c", help="Branch to compare against"),
    dialect: str = typer.Option("postgres", "--dialect", "-d", help="SQL dialect"),
    database_url: str = typer.Option(None, "--db", help="Database connection URL"),
    threshold: float = typer.Option(1.5, "--threshold", "-t", help="Cost increase threshold (e.g. 1.5 = 50% increase)"),
    path: Path = typer.Option(Path("."), "--path", help="Path to search for SQL files"),
):
    """Check for SQL regressions by comparing plans between branches."""
    from .regression import check_regressions, RegressionError

    try:
        result = check_regressions(
            compare_branch=compare_branch,
            dialect=dialect,
            database_url=database_url,
            threshold=threshold,
            path=path,
        )

        if result.regressions:
            console.print(Panel.fit(
                f"[red]❌ {len(result.regressions)} regression(s) detected[/red]",
                title="Regression Check",
                border_style="red",
            ))
            for reg in result.regressions:
                console.print(f"  [red]• {reg.file}:[/red] {reg.old_cost:.0f} → {reg.new_cost:.0f} ({reg.ratio:.1f}x)")
                for warn in reg.warnings:
                    console.print(f"    [yellow]⚠ {warn}[/yellow]")
            raise typer.Exit(1)
        else:
            console.print(Panel.fit(
                "[green]✅ No regressions detected[/green]",
                title="Regression Check",
                border_style="green",
            ))
            for ok in result.passed:
                console.print(f"  [green]✓[/green] {ok.file}: {ok.cost:.0f}")

    except RegressionError as e:
        console.print(f"[red]Regression Check Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def diff(
    old_sql: str = typer.Argument(..., help="Old SQL query"),
    new_sql: str = typer.Argument(..., help="New SQL query"),
    dialect: str = typer.Option("postgres", "--dialect", "-d", help="SQL dialect"),
):
    """Compare two SQL queries side by side."""
    from .diff import diff_plans, DiffError

    try:
        result = diff_plans(old_sql, new_sql, dialect)
        console.print(result.render())
    except DiffError as e:
        console.print(f"[red]Diff Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def version():
    """Show version information."""
    console.print(f"vizql {__version__}")


def main():
    app()


if __name__ == "__main__":
    main()