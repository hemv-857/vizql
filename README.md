# vizql — Visual SQL Explainer

[![PyPI](https://img.shields.io/pypi/v/vizql)](https://pypi.org/project/vizql/)
[![Python](https://img.shields.io/pypi/pyversions/vizql)](https://pypi.org/project/vizql/)
[![License](https://img.shields.io/pypi/l/vizql)](LICENSE)
[![Build](https://github.com/hemv-857/vizql/actions/workflows/sql-regression.yml/badge.svg)](https://github.com/hemv-857/vizql/actions)

**vizql** parses SQL, builds an execution plan, estimates costs per dialect, and renders the result as a terminal diagram, Mermaid flowchart, Graphviz SVG, or JSON — without requiring a database connection.

---

## Overview

| Capability | Description |
|------------|-------------|
| **Parsing** | SQLGlot-based parser supporting PostgreSQL, MySQL, Snowflake, BigQuery, DuckDB, Spark, T-SQL |
| **Planning** | Logical-to-physical plan translation (Seq/Index Scan, Hash/Nested Loop/Merge Join, Hash/Sort Aggregate, Sort, Limit, CTE) |
| **Cost Modeling** | Per-dialect cost models with CPU, I/O, and memory estimates |
| **Simulation** | Step-by-step row-flow animation with progress tracking |
| **Analysis** | Automatic bottleneck detection (seq scans, join spills, sort spills) and index recommendations |
| **Output Formats** | Terminal (Rich), Mermaid, Graphviz DOT/SVG, JSON |
| **CI/CD** | GitHub Action for regression detection on pull requests |

---

## Installation

```bash
# Recommended: isolated CLI
pipx install vizql

# Or standard install
pip install vizql
```

Requires Python 3.10+.

---

## Usage

### Explain a Query

```bash
# From argument
vizql explain "SELECT u.name, COUNT(*) FROM users u JOIN orders o ON u.id=o.user_id GROUP BY u.name"

# From file
vizql explain --file query.sql --dialect postgres

# From stdin
cat query.sql | vizql explain
```

### Output Formats

```bash
vizql explain "SELECT 1" --format terminal   # Rich terminal (default)
vizql explain "SELECT 1" --format mermaid    # Mermaid flowchart
vizql explain "SELECT 1" --format graphviz   # Graphviz DOT
vizql explain "SELECT 1" --format json       # Machine-readable JSON
```

### Detect Regressions (CI/CD)

```bash
# Compare changed SQL files against main branch
vizql check --compare main --db postgresql://localhost/mydb

# Custom threshold (default 1.5 = 50% cost increase)
vizql check --threshold 2.0 --db $DATABASE_URL

# Exit code 1 on regression for pipeline gating
vizql check --compare main --db $DATABASE_URL || exit 1
```

### Compare Two Queries

```bash
vizql diff "SELECT * FROM users" "SELECT * FROM users WHERE id = 1"
```

---

## Example Output

### Terminal

```
$ vizql explain "SELECT u.name FROM users u JOIN orders o ON u.id=o.user_id WHERE u.status='active'"

Seq Scan on users
  Rows: 50.0K | Cost: 230 | Filter: status='active'
  Warning: No index on status column

  ↓

Hash Join (INNER)
  Rows: 180.0K | Cost: 850 | Condition: u.id = o.user_id
  Warning: Build side 1.2M rows — may spill to disk

  ↓

Hash Aggregate
  Rows: 42.0K | Cost: 1,050 | Group By: u.name

Total Cost: 2,130 | Max Memory: 340 MB
```

### Mermaid (renders in GitHub, GitLab, Notion, Obsidian)

```mermaid
flowchart TD
    N1["Seq Scan on users<br/>Rows: 50,000<br/>Cost: 230<br/>Filter: status='active'"]
    N2["Hash Join (INNER)<br/>Rows: 180,000<br/>Cost: 850<br/>Condition: u.id = o.user_id"]
    N3["Hash Aggregate<br/>Rows: 42,000<br/>Cost: 1,050<br/>Group By: u.name"]

    N1 --> N2
    N2 --> N3

    style N1 fill:#ff6b6b,color:#fff
    style N2 fill:#ffe66d,color:#000
    style N3 fill:#a8e6cf,color:#000
```

### JSON

```json
{
  "plan": {
    "type": "Hash Aggregate",
    "est_rows": 42000,
    "est_cost": 1050,
    "children": [...]
  },
  "frames": [...],
  "advisor": {
    "warnings": ["Seq Scan on users: 50K rows scanned"],
    "suggestions": ["CREATE INDEX ON users(status) -- Est. impact: 95% faster"],
    "index_recommendations": [...]
  }
}
```

---

## CI/CD Integration

Add to `.github/workflows/sql-check.yml`:

```yaml
name: SQL Regression Check
on:
  pull_request:
    paths:
      - '*.sql'
      - 'migrations/*.sql'
      - 'queries/*.sql'

jobs:
  sql-regression:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: testdb
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports: [5432:5432]
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install vizql
      - run: |
          for i in {1..30}; do
            pg_isready -h localhost -p 5432 -U test && break
            sleep 1
          done
      - run: vizql check --compare ${{ github.base_ref }} --db postgresql://test:test@localhost:5432/testdb
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        vizql CLI                              │
├─────────────────────────────────────────────────────────────┤
│  sqlglot (parse) → planner (logical plan) → optimizer       │
│         │                              │                    │
│         ▼                              ▼                    │
│  ┌─────────────┐              ┌─────────────────┐          │
│  │ Dialect     │              │ Cost model      │          │
│  │ normalizer  │              │ (per-dialect)   │          │
│  └─────────────┘              └─────────────────┘          │
│         │                              │                    │
│         └──────────────┬───────────────┘                    │
│                        ▼                                    │
│         ┌─────────────────────────┐                         │
│         │  Execution Simulator    │  ← animates row counts │
│         │  (no DB required)       │  ← estimates via stats │
│         └─────────────────────────┘                         │
│                        │                                    │
│         ┌──────────────┼──────────────┐                     │
│         ▼              ▼              ▼                     │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐                 │
│   │ Mermaid  │  │  SVG/    │  │  TUI     │  ← three renders│
│   │  /DOT    │  │  Graphviz│  │  (Rich)  │                 │
│   └──────────┘  └──────────┘  └──────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

**Zero-DB mode:** Execution is simulated using `sqlglot` for parsing, statistics heuristics for cardinality estimation, and per-dialect cost models — no live database connection required.

---

## Configuration

Create `~/.vizql.yaml`:

```yaml
dialect: postgres
database_url: postgresql://localhost/mydb
threshold: 1.5
format: terminal
costs: true
warnings: true
suggestions: true
```

Environment variables (override config):

```bash
export VIZQL_DATABASE_URL=postgresql://...
export VIZQL_DIALECT=postgres
export VIZQL_THRESHOLD=1.5
```

---

## Development

```bash
git clone https://github.com/hemv-857/vizql
cd vizql

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

# Run tests
pytest

# Lint and format
ruff check .
ruff format .

# Type check
mypy src/vizql
```

### Adding a Dialect

1. Implement `BaseCostModel` in `src/vizql/cost_model/<dialect>.py`
2. Register in `src/vizql/cost_model/__init__.py`
3. Add tests in `tests/`

---

## License

MIT — see [LICENSE](LICENSE).

---

## Dependencies

- [sqlglot](https://github.com/tobymao/sqlglot) — SQL parsing and transpilation
- [Rich](https://github.com/Textualize/rich) — Terminal formatting
- [Mermaid](https://mermaid.js.org/) — Diagram rendering
- [Graphviz](https://graphviz.org/) — Graph visualization

---

## Support

- **Issues:** [GitHub Issues](https://github.com/hemv-857/vizql/issues)
- **Discussions:** [GitHub Discussions](https://github.com/hemv-857/vizql/discussions)
- **Author:** [Hemang Varshney](https://github.com/hemv-857)