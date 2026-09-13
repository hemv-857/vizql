"""SQL parsing using sqlglot."""

from dataclasses import dataclass
from typing import Any, Optional

import sqlglot
from sqlglot import exp, parse_one
from sqlglot.optimizer import optimize
from sqlglot.errors import ErrorLevel


@dataclass
class ParseError(Exception):
    """SQL parse error."""
    message: str


@dataclass
class ParsedQuery:
    """Parsed SQL query with metadata."""
    ast: exp.Expression
    dialect: str
    tables: list[str]
    columns: list[str]
    cte_names: list[str]


# Dialect mapping
DIALECT_MAP = {
    "postgres": "postgres",
    "postgresql": "postgres",
    "pg": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "duckdb": "duckdb",
    "spark": "spark",
    "tsql": "tsql",
    "sqlite": "sqlite",
}


def normalize_dialect(dialect: str) -> str:
    """Normalize dialect name."""
    return DIALECT_MAP.get(dialect.lower(), "postgres")


def extract_tables(ast: exp.Expression) -> list[str]:
    """Extract table names from AST."""
    tables = []
    for table in ast.find_all(exp.Table):
        if table.name:
            tables.append(table.name)
    return list(set(tables))


def extract_columns(ast: exp.Expression) -> list[str]:
    """Extract column names from AST."""
    columns = []
    for col in ast.find_all(exp.Column):
        if col.name:
            columns.append(col.name)
    return list(set(columns))


def extract_cte_names(ast: exp.Expression) -> list[str]:
    """Extract CTE names from AST."""
    ctes = []
    for cte in ast.find_all(exp.CTE):
        if cte.alias:
            ctes.append(cte.alias)
    return ctes


def parse_sql(sql: str, dialect: str = "postgres") -> ParsedQuery:
    """Parse SQL query and return structured representation.

    Args:
        sql: SQL query string
        dialect: SQL dialect (postgres, mysql, snowflake, etc.)

    Returns:
        ParsedQuery with AST and metadata

    Raises:
        ParseError: If SQL cannot be parsed
    """
    normalized = normalize_dialect(dialect)

    try:
        # Parse with sqlglot
        ast = parse_one(sql, read=normalized, error_level=ErrorLevel.RAISE)

        # Optimize (optional, can be disabled for raw plan)
        # ast = optimize(ast, schema={})

        return ParsedQuery(
            ast=ast,
            dialect=normalized,
            tables=extract_tables(ast),
            columns=extract_columns(ast),
            cte_names=extract_cte_names(ast),
        )

    except Exception as e:
        raise ParseError(f"Failed to parse SQL: {e}")


def transpile_sql(sql: str, from_dialect: str, to_dialect: str) -> str:
    """Transpile SQL between dialects."""
    from_norm = normalize_dialect(from_dialect)
    to_norm = normalize_dialect(to_dialect)

    try:
        result = sqlglot.transpile(sql, read=from_norm, write=to_norm)
        return result[0] if result else sql
    except Exception as e:
        raise ParseError(f"Failed to transpile SQL: {e}")


def get_query_type(sql: str, dialect: str = "postgres") -> str:
    """Get the type of SQL query (SELECT, INSERT, etc.)."""
    parsed = parse_sql(sql, dialect)
    ast = parsed.ast

    if isinstance(ast, exp.Select):
        return "SELECT"
    elif isinstance(ast, exp.Insert):
        return "INSERT"
    elif isinstance(ast, exp.Update):
        return "UPDATE"
    elif isinstance(ast, exp.Delete):
        return "DELETE"
    elif isinstance(ast, exp.Create):
        return "CREATE"
    elif isinstance(ast, exp.Drop):
        return "DROP"
    elif isinstance(ast, exp.Alter):
        return "ALTER"
    else:
        return "OTHER"


def is_read_only(sql: str, dialect: str = "postgres") -> bool:
    """Check if query is read-only (SELECT, WITH)."""
    qtype = get_query_type(sql, dialect)
    return qtype in ("SELECT",)