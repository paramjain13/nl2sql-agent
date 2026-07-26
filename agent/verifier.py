"""SQL AST verifier — Phase 2, Step 1: pure AST extraction via sqlglot.

Not wired into the agent graph yet. This just parses a SQL string and pulls
out the tables, column references, and join conditions so we can eyeball the
extraction before adding schema validation (Step 2: do these tables/columns
actually exist? are the joins on real FK paths?).
"""
from dataclasses import dataclass
from typing import List, Optional

import sqlglot
from sqlglot import exp


@dataclass
class TableRef:
    name: str                 # base table name as written
    alias: Optional[str]      # alias if the query gave one, else None


@dataclass
class ColumnRef:
    table: Optional[str]      # qualifier as written (alias or table name), None if unqualified
    column: str


@dataclass
class JoinCondition:
    left: ColumnRef
    right: ColumnRef
    raw: str                  # the equality condition as SQL text


@dataclass
class ParsedSQL:
    tables: List[TableRef]
    columns: List[ColumnRef]
    joins: List[JoinCondition]


def parse_sql(sql: str, dialect: str = "sqlite") -> ParsedSQL:
    """Parse `sql` into an AST and extract tables, column refs, and join conditions.

    Join extraction covers `ON <eq>` (including compound `AND`-ed equalities)
    and `USING (col)`. Non-equality join conditions (e.g. `<`, `BETWEEN`) are
    not extracted here — this step is about the equi-join column graph.
    """
    tree = sqlglot.parse_one(sql, dialect=dialect)

    tables = [
        TableRef(name=t.name, alias=t.alias or None) for t in tree.find_all(exp.Table)
    ]

    columns = [
        ColumnRef(table=c.table or None, column=c.name) for c in tree.find_all(exp.Column)
    ]

    joins: List[JoinCondition] = []
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is not None:
            for eq in on.find_all(exp.EQ):
                left, right = eq.this, eq.expression
                if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                    joins.append(
                        JoinCondition(
                            left=ColumnRef(table=left.table or None, column=left.name),
                            right=ColumnRef(table=right.table or None, column=right.name),
                            raw=eq.sql(dialect=dialect),
                        )
                    )

        using = j.args.get("using")
        if using:
            joined_table = j.this.alias_or_name if isinstance(j.this, exp.Table) else None
            for ident in using:
                joins.append(
                    JoinCondition(
                        left=ColumnRef(table=None, column=ident.name),
                        right=ColumnRef(table=joined_table, column=ident.name),
                        raw=f"USING ({ident.name})",
                    )
                )

    return ParsedSQL(tables=tables, columns=columns, joins=joins)


if __name__ == "__main__":
    # Quick eyeball test: parse 3 queries written against our fintech bank
    # schema (data/fintech/bank.sqlite) and print what got extracted.
    EXAMPLE_QUERIES = [
        # 1. single table, no joins
        """
        SELECT client_id, gender
        FROM client
        WHERE gender = 'F'
        """,
        # 2. two joins, ON with qualified columns
        """
        SELECT c.client_id, a.account_id, a.frequency
        FROM client c
        JOIN disp d ON d.client_id = c.client_id
        JOIN account a ON a.account_id = d.account_id
        """,
        # 3. join + USING + aggregation
        """
        SELECT d.A2 AS district_name, COUNT(a.account_id) AS num_accounts
        FROM district d
        JOIN account a ON a.district_id = d.district_id
        GROUP BY d.A2
        """,
    ]

    for i, sql in enumerate(EXAMPLE_QUERIES, 1):
        print(f"\n=== Query {i} ===")
        print(sql.strip())
        parsed = parse_sql(sql)

        print("\n-- tables --")
        for t in parsed.tables:
            print(f"  {t.name}" + (f" AS {t.alias}" if t.alias else ""))

        print("-- columns --")
        for c in parsed.columns:
            print(f"  {c.table + '.' if c.table else ''}{c.column}")

        print("-- joins --")
        if not parsed.joins:
            print("  (none)")
        for j in parsed.joins:
            left = f"{j.left.table + '.' if j.left.table else ''}{j.left.column}"
            right = f"{j.right.table + '.' if j.right.table else ''}{j.right.column}"
            print(f"  {left} = {right}")
