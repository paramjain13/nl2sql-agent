"""SQL AST verifier — Phase 2, Step 1: pure AST extraction via sqlglot.

Not wired into the agent graph yet. This just parses a SQL string and pulls
out the tables, column references, and join conditions so we can eyeball the
extraction before adding schema validation (Step 2: do these tables/columns
actually exist? are the joins on real FK paths?).
"""
import difflib
import re
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import sqlglot
from sqlglot import exp


@dataclass
class TableRef:
    name: str                 # base table name as written
    alias: Optional[str]      # alias if the query gave one, else None
    quoted: bool = False      # True if the table name was quoted in the source SQL


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
    # AST context `validate()` needs to tell "real table" apart from "opaque
    # name that isn't in the schema on purpose" — see parse_sql() below.
    cte_names: Set[str] = field(default_factory=set)        # WITH <name> AS (...)
    derived_aliases: Set[str] = field(default_factory=set)  # FROM (SELECT ...) AS <alias>
    select_aliases: Set[str] = field(default_factory=set)   # SELECT ... AS <alias>


def parse_sql(sql: str, dialect: str = "sqlite") -> ParsedSQL:
    """Parse `sql` into an AST and extract tables, column refs, and join conditions.

    Join extraction covers `ON <eq>` (including compound `AND`-ed equalities)
    and `USING (col)`. Non-equality join conditions (e.g. `<`, `BETWEEN`) are
    not extracted here — this step is about the equi-join column graph.
    """
    tree = sqlglot.parse_one(sql, dialect=dialect)

    tables = [
        TableRef(
            name=t.name,
            alias=t.alias or None,
            quoted=bool(getattr(t.this, "quoted", False)),
        )
        for t in tree.find_all(exp.Table)
    ]

    columns = [
        ColumnRef(table=c.table or None, column=c.name) for c in tree.find_all(exp.Column)
    ]

    # A CTE name shows up in tree.find_all(exp.Table) like any other table
    # reference (sqlglot doesn't distinguish them there) — exp.CTE is the only
    # place the name is unambiguous. A subquery alias never appears as
    # exp.Table at all (it's exp.Subquery), so it needs no such reconciliation,
    # but its alias still shows up as a column qualifier and must be skippable.
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE) if c.alias_or_name}
    derived_aliases = {
        s.alias_or_name.lower() for s in tree.find_all(exp.Subquery) if s.alias_or_name
    }
    select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}

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

    # Implicit joins (old-style comma joins): sqlglot represents `FROM a, b`
    # as a Join(kind=CROSS) with no `on`/`using` — the actual equality lives
    # in the WHERE clause instead, as a plain column = column equality.
    for where in tree.find_all(exp.Where):
        for eq in where.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                joins.append(
                    JoinCondition(
                        left=ColumnRef(table=left.table or None, column=left.name),
                        right=ColumnRef(table=right.table or None, column=right.name),
                        raw=eq.sql(dialect=dialect),
                    )
                )

    return ParsedSQL(
        tables=tables,
        columns=columns,
        joins=joins,
        cte_names=cte_names,
        derived_aliases=derived_aliases,
        select_aliases=select_aliases,
    )


# --- Schema introspection (Step 2 groundwork): what does the DB actually look like? ---


@dataclass
class ForeignKey:
    table: str          # table the FK column lives on (lowercase)
    column: str          # the local FK column (lowercase)
    ref_table: str       # referenced table (lowercase)
    ref_column: str      # referenced column (lowercase)
    inferred: bool = False  # True if guessed by naming convention, not a declared FK


def _infer_foreign_keys(
    tables: Dict[str, Set[str]], primary_keys: Dict[str, List[str]]
) -> List[ForeignKey]:
    """Naming-convention fallback for schemas with no declared FKs.

    A column whose name matches another table's single-column primary key
    becomes an edge to that table. Never infers a self-edge (a table's own PK
    column trivially "matches" its own PK name).
    """
    single_col_pk = {t: pks[0] for t, pks in primary_keys.items() if len(pks) == 1}

    inferred: List[ForeignKey] = []
    for table, columns in tables.items():
        for column in columns:
            for ref_table, pk_col in single_col_pk.items():
                if ref_table == table:
                    continue
                if column == pk_col:
                    inferred.append(
                        ForeignKey(
                            table=table,
                            column=column,
                            ref_table=ref_table,
                            ref_column=pk_col,
                            inferred=True,
                        )
                    )
    return inferred


@dataclass
class Schema:
    tables: Dict[str, Set[str]]            # table_lower -> {column names, lowercase}
    primary_keys: Dict[str, List[str]]     # table_lower -> PK column names, in PK order
    foreign_keys: List[ForeignKey]
    graph: Dict[str, Set[str]] = field(default_factory=dict, init=False)  # undirected adjacency

    def __post_init__(self) -> None:
        self.graph = {t: set() for t in self.tables}
        for fk in self.foreign_keys:
            self.graph.setdefault(fk.table, set()).add(fk.ref_table)
            self.graph.setdefault(fk.ref_table, set()).add(fk.table)

    @classmethod
    def from_sqlite(cls, db_path: str) -> "Schema":
        """Read a SQLite DB's tables, columns, primary keys, and FK edges."""
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            table_names = [row[0] for row in cur.fetchall()]

            tables: Dict[str, Set[str]] = {}
            primary_keys: Dict[str, List[str]] = {}
            foreign_keys: List[ForeignKey] = []

            for table in table_names:
                table_lower = table.lower()

                cur.execute(f'PRAGMA table_info("{table}")')
                # each row: (cid, name, type, notnull, dflt_value, pk)
                col_rows = cur.fetchall()
                tables[table_lower] = {row[1].lower() for row in col_rows}
                pk_rows = sorted((row for row in col_rows if row[5] > 0), key=lambda row: row[5])
                primary_keys[table_lower] = [row[1].lower() for row in pk_rows]

                cur.execute(f'PRAGMA foreign_key_list("{table}")')
                # each row: (id, seq, table, from, to, on_update, on_delete, match)
                for fk_row in cur.fetchall():
                    foreign_keys.append(
                        ForeignKey(
                            table=table_lower,
                            column=fk_row[3].lower(),
                            ref_table=fk_row[2].lower(),
                            ref_column=fk_row[4].lower(),
                        )
                    )

            if not foreign_keys:
                # No table declared any FK — fall back to naming-convention inference.
                foreign_keys = _infer_foreign_keys(tables, primary_keys)

            return cls(tables=tables, primary_keys=primary_keys, foreign_keys=foreign_keys)
        finally:
            conn.close()

    def describe(self) -> None:
        """Print table count, FK count, and each FK edge as t.col -> ref_t.ref_col."""
        print(f"Tables: {len(self.tables)}")
        print(f"Foreign keys: {len(self.foreign_keys)}")
        for fk in self.foreign_keys:
            suffix = " (inferred)" if fk.inferred else ""
            print(f"  {fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}{suffix}")

    def has_table(self, table: str) -> bool:
        return table.lower() in self.tables

    def has_column(self, table: str, column: str) -> bool:
        return column.lower() in self.tables.get(table.lower(), set())

    def columns_of(self, table: str) -> Set[str]:
        return self.tables.get(table.lower(), set())

    def fks_between(self, t1: str, t2: str) -> List[ForeignKey]:
        """All FKs linking t1 and t2, in either direction."""
        t1, t2 = t1.lower(), t2.lower()
        return [
            fk
            for fk in self.foreign_keys
            if (fk.table == t1 and fk.ref_table == t2)
            or (fk.table == t2 and fk.ref_table == t1)
        ]

    def matches_fk(self, t1: str, c1: str, t2: str, c2: str) -> Optional[ForeignKey]:
        """The ForeignKey for the exact column pair (t1.c1, t2.c2), checked in
        either direction, or None if that pair isn't a declared/inferred edge."""
        t1, c1, t2, c2 = t1.lower(), c1.lower(), t2.lower(), c2.lower()
        for fk in self.foreign_keys:
            if fk.table == t1 and fk.column == c1 and fk.ref_table == t2 and fk.ref_column == c2:
                return fk
            if fk.table == t2 and fk.column == c2 and fk.ref_table == t1 and fk.ref_column == c1:
                return fk
        return None

    def fk_path(self, t1: str, t2: str, max_hops: int = 3) -> Optional[List[str]]:
        """Shortest path from t1 to t2 over the undirected FK graph (BFS).

        Returns a list of table names from t1 to t2 inclusive, or None if no
        path exists within max_hops edges.
        """
        t1, t2 = t1.lower(), t2.lower()
        if t1 not in self.graph or t2 not in self.graph:
            return None
        if t1 == t2:
            return [t1]

        visited = {t1}
        queue = deque([(t1, [t1])])  # (current table, path taken to reach it)

        while queue:
            current, path = queue.popleft()
            if len(path) - 1 == max_hops:
                continue  # at the hop budget already — don't expand further
            for neighbor in self.graph[current]:
                if neighbor in visited:
                    continue
                new_path = path + [neighbor]
                if neighbor == t2:
                    return new_path
                visited.add(neighbor)
                queue.append((neighbor, new_path))

        return None


# --- Validation (Step 2): do the tables/columns ParsedSQL references actually exist? ---


# A representative (not exhaustive) set of ANSI SQL reserved words that
# sqlite's grammar still accepts as bare identifiers — legal, but a footgun.
# `order` is the concrete, motivating case: it's a real table in bank.sqlite.
RESERVED_WORDS = {
    "order", "group", "select", "table", "index", "key", "where", "from",
    "join", "on", "and", "or", "not", "in", "exists", "between", "like",
    "is", "null", "case", "when", "then", "else", "end", "union", "all",
    "distinct", "having", "limit", "values", "into", "as", "by",
}


@dataclass
class Issue:
    kind: str  # "missing_table" | "missing_column" | "ambiguous_column" |
    # "invalid_join" | "cartesian_join" | "reserved_word" | "parse_error"
    message: str
    severity: str = "error"  # "error" | "warning"
    suggestion: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    ok: bool
    issues: List[Issue]

    def feedback(self) -> str:
        """Render all issues as terse, one-line-each text."""
        if not self.issues:
            return "OK — no issues."
        lines = []
        for issue in self.issues:
            line = f"[{issue.severity.upper()}] {issue.kind}: {issue.message}"
            if issue.suggestion:
                line += f" (suggestion: {issue.suggestion})"
            lines.append(line)
        return "\n".join(lines)


def _suggest_for_missing_column(column: str, checked_tables: List[str], schema: Schema) -> Optional[str]:
    """Suggest a close column-name match on the checked tables, or — failing
    that — a table elsewhere in the schema that actually has this exact column."""
    col = column.lower()

    candidate_cols: Set[str] = set()
    for t in checked_tables:
        candidate_cols |= schema.columns_of(t)
    close = difflib.get_close_matches(col, candidate_cols, n=1)
    if close:
        return f"did you mean '{close[0]}'?"

    owners = sorted(
        t for t in schema.tables if col in schema.columns_of(t) and t not in checked_tables
    )
    if owners:
        return f"column '{col}' exists on table '{owners[0]}' instead"

    return None


def _resolve_tables(parsed: ParsedSQL, schema: Schema) -> "tuple[Dict[str, str], List[Issue]]":
    """Step 1: map each alias-or-name used in the query to a real schema table.

    CTE names are skipped entirely (they're not real tables — nothing to
    validate). Derived-table aliases never reach here as TableRefs at all
    (sqlglot represents them as exp.Subquery, not exp.Table).
    """
    alias_to_table: Dict[str, str] = {}
    issues: List[Issue] = []

    for t in parsed.tables:
        if t.name.lower() in parsed.cte_names:
            continue

        key = (t.alias or t.name).lower()
        base_name = t.name.lower()

        if schema.has_table(base_name):
            alias_to_table[key] = base_name
            if base_name in RESERVED_WORDS and not t.quoted:
                issues.append(
                    Issue(
                        kind="reserved_word",
                        message=f"table '{t.name}' is a reserved word and was referenced unquoted",
                        severity="warning",
                        suggestion=f"quote it, e.g. `{base_name}`",
                        detail={"table": base_name},
                    )
                )
            continue

        suggestion = None
        close = difflib.get_close_matches(base_name, schema.tables.keys(), n=1)
        if close:
            suggestion = f"did you mean table '{close[0]}'?"
        issues.append(
            Issue(
                kind="missing_table",
                message=f"table '{t.name}' does not exist in the schema",
                suggestion=suggestion,
                detail={"table": t.name},
            )
        )

    return alias_to_table, issues


def _check_columns(
    parsed: ParsedSQL, schema: Schema, alias_to_table: Dict[str, str]
) -> List[Issue]:
    """Step 2: does each referenced column exist on (or unambiguously belong
    to) the tables resolved in Step 1? No join-path checking yet."""
    issues: List[Issue] = []
    query_tables = sorted(set(alias_to_table.values()))
    # Qualifiers we intentionally can't validate — not an error, just opaque.
    skip_qualifiers = parsed.derived_aliases | parsed.cte_names

    for c in parsed.columns:
        if c.column == "*":
            continue
        if c.column.lower() in parsed.select_aliases:
            continue

        if c.table:
            qualifier = c.table.lower()
            if qualifier in skip_qualifiers:
                continue
            table = alias_to_table.get(qualifier)
            if table is None:
                # Alias didn't resolve to a real table — already reported as
                # missing_table in Step 1 (or an alias outside this function's
                # scope). Nothing more useful to say about its columns.
                continue
            if not schema.has_column(table, c.column):
                issues.append(
                    Issue(
                        kind="missing_column",
                        message=f"column '{c.table}.{c.column}' does not exist",
                        suggestion=_suggest_for_missing_column(c.column, [table], schema),
                        detail={"table": table, "column": c.column},
                    )
                )
        else:
            owners = [t for t in query_tables if schema.has_column(t, c.column)]
            if len(owners) == 0:
                issues.append(
                    Issue(
                        kind="missing_column",
                        message=f"column '{c.column}' does not exist on any table in this query",
                        suggestion=_suggest_for_missing_column(c.column, query_tables, schema),
                        detail={"column": c.column, "checked_tables": query_tables},
                    )
                )
            elif len(owners) > 1:
                issues.append(
                    Issue(
                        kind="ambiguous_column",
                        message=f"column '{c.column}' is ambiguous — present in tables: {', '.join(owners)}",
                        suggestion=None,
                        detail={"column": c.column, "candidates": owners},
                    )
                )

    return issues


def _fk_chain_conditions(schema: Schema, path: List[str]) -> List[str]:
    """For a table path (as returned by Schema.fk_path), the ON condition
    linking each consecutive pair, oriented left-table-first."""
    conditions = []
    for left, right in zip(path, path[1:]):
        fk = schema.fks_between(left, right)[0]  # fk_path guarantees adjacency
        if fk.table == left:
            conditions.append(f"{fk.table}.{fk.column} = {fk.ref_table}.{fk.ref_column}")
        else:
            conditions.append(f"{fk.ref_table}.{fk.ref_column} = {fk.table}.{fk.column}")
    return conditions


def _check_joins(
    parsed: ParsedSQL, schema: Schema, alias_to_table: Dict[str, str]
) -> List[Issue]:
    """Step 3: does each equi-join condition sit on a real FK path?

    Covers explicit `JOIN ... ON` / `USING` and implicit comma joins
    (`FROM a, b WHERE a.id = b.a_id`) alike — parse_sql() puts both into
    parsed.joins, so this doesn't need to know which syntax was used.
    """
    issues: List[Issue] = []

    for j in parsed.joins:
        if j.left.table is None or j.right.table is None:
            continue  # unqualified join side — resolving it needs Step-2-style
            # ownership search, out of scope here
        t1 = alias_to_table.get(j.left.table.lower())
        t2 = alias_to_table.get(j.right.table.lower())
        c1, c2 = j.left.column, j.right.column
        if t1 is None or t2 is None:
            continue  # unresolved table — already reported in Step 1
        if t1 == t2:
            continue  # same-table equality, not a cross-table join

        if schema.matches_fk(t1, c1, t2, c2) is not None:
            continue  # sits on a real (or inferred) FK — valid

        between = schema.fks_between(t1, t2)
        if between:
            fk = between[0]
            issues.append(
                Issue(
                    kind="invalid_join",
                    message=f"'{j.raw}' does not match the FK between '{t1}' and '{t2}'",
                    suggestion=f"join on {fk.table}.{fk.column} = {fk.ref_table}.{fk.ref_column}",
                    detail={
                        "reason": "wrong_columns",
                        "t1": t1, "c1": c1, "t2": t2, "c2": c2,
                        "real_fk": {
                            "table": fk.table, "column": fk.column,
                            "ref_table": fk.ref_table, "ref_column": fk.ref_column,
                        },
                    },
                )
            )
            continue

        path = schema.fk_path(t1, t2)
        if path:
            issues.append(
                Issue(
                    kind="invalid_join",
                    message=(
                        f"no direct FK between '{t1}' and '{t2}' — "
                        f"needs intermediate table(s): {' -> '.join(path)}"
                    ),
                    suggestion=" AND ".join(_fk_chain_conditions(schema, path)),
                    detail={"reason": "needs_bridge", "t1": t1, "t2": t2, "path": path},
                )
            )
            continue

        issues.append(
            Issue(
                kind="invalid_join",
                message=f"no FK path found between '{t1}' and '{t2}'",
                suggestion=None,
                detail={"reason": "no_path", "t1": t1, "t2": t2},
            )
        )

    return issues


class _UnionFind:
    """Minimal disjoint-set over a fixed universe of items."""

    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _check_cartesian(parsed: ParsedSQL, alias_to_table: Dict[str, str]) -> List[Issue]:
    """Step 4: do the query's tables form one connected group, or more than
    one (i.e. some table pair has no join condition linking them at all —
    an accidental cross product)?

    Any equi-join condition counts here, valid FK or not — this is about
    whether the query even *attempts* to relate every table, not whether it
    does so correctly (that's Step 3's job).
    """
    tables = sorted(set(alias_to_table.values()))
    if len(tables) < 2:
        return []

    uf = _UnionFind(tables)
    for j in parsed.joins:
        if j.left.table is None or j.right.table is None:
            continue
        t1 = alias_to_table.get(j.left.table.lower())
        t2 = alias_to_table.get(j.right.table.lower())
        if t1 is None or t2 is None or t1 == t2:
            continue
        uf.union(t1, t2)

    groups: Dict[str, List[str]] = {}
    for t in tables:
        groups.setdefault(uf.find(t), []).append(t)

    if len(groups) > 1:
        group_list = sorted(sorted(g) for g in groups.values())
        return [
            Issue(
                kind="cartesian_join",
                message=(
                    f"query's tables form {len(group_list)} disconnected groups "
                    f"{group_list} — likely an accidental cross join"
                ),
                severity="warning",
                suggestion=None,
                detail={"groups": group_list},
            )
        ]
    return []


def validate(parsed: ParsedSQL, schema: Schema) -> ValidationResult:
    """Validate a parsed query's tables, columns, and joins against a real schema.

    Step 1: resolve tables/aliases, flagging any that aren't in the schema
    (also warns if a real table name is a reserved word used unquoted).
    Step 2: for each column reference, check it exists on its resolved table
    (qualified) or exists on exactly one of the query's tables (unqualified).
    Step 3: for each equi-join, check it sits on a real FK — either directly,
    or (if the tables have a different real FK) flag the wrong columns, or
    (if they're only reachable through other tables) suggest the FK bridge.
    Step 4: do the query's tables form a single connected group, or does some
    pair have no join at all (an accidental cross product)?

    `ok` reflects only error-severity issues — warnings (cartesian_join,
    reserved_word) don't fail validation on their own.
    """
    alias_to_table, issues = _resolve_tables(parsed, schema)
    issues += _check_columns(parsed, schema, alias_to_table)
    issues += _check_joins(parsed, schema, alias_to_table)
    issues += _check_cartesian(parsed, alias_to_table)
    ok = not any(i.severity == "error" for i in issues)
    return ValidationResult(ok=ok, issues=issues)


def autofix_reserved_words(sql: str, dialect: str = "sqlite") -> str:
    """Backtick any bare (unquoted) reserved-word identifiers in `sql`.

    Parses with sqlglot and walks actual Identifier nodes, so real keyword
    usage (e.g. the ORDER in ORDER BY) is never touched — only genuine
    table/column names that happen to collide with a reserved word.
    """
    tree = sqlglot.parse_one(sql, dialect=dialect)
    touched = False
    for ident in tree.find_all(exp.Identifier):
        if not ident.quoted and ident.this.lower() in RESERVED_WORDS:
            ident.set("quoted", True)
            touched = True

    if not touched:
        return sql

    rewritten = tree.sql(dialect=dialect)
    # sqlglot emits ANSI double-quotes for sqlite; convert the specific
    # reserved-word quotations to backticks, as requested.
    for word in RESERVED_WORDS:
        rewritten = re.sub(rf'"({re.escape(word)})"', r"`\1`", rewritten, flags=re.IGNORECASE)
    return rewritten


def verify(sql: str, schema: Schema, dialect: str = "sqlite") -> ValidationResult:
    """Parse + validate `sql` in one call. Parse failures never raise — they
    come back as a single `parse_error` ValidationResult instead."""
    try:
        parsed = parse_sql(sql, dialect=dialect)
    except Exception as e:
        return ValidationResult(
            ok=False,
            issues=[
                Issue(
                    kind="parse_error",
                    message=str(e),
                    severity="error",
                    suggestion=None,
                    detail={"sql": sql},
                )
            ],
        )
    return validate(parsed, schema)


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

    print("\n=== Schema: data/fintech/bank.sqlite ===")
    schema = Schema.from_sqlite("data/fintech/bank.sqlite")
    schema.describe()

    print("\n=== fk_path ===")
    for a, b in [("client", "loan"), ("card", "district"), ("trans", "client")]:
        print(f"  {a} -> {b}: {schema.fk_path(a, b)}")

    print("\n=== autofix_reserved_words ===")
    fixed = autofix_reserved_words(
        "SELECT o.order_id FROM order o JOIN account a ON o.account_id = a.account_id "
        "ORDER BY o.order_id"
    )
    print(f"  {fixed}")

    # A schema with an unreachable table grafted on, just to exercise the
    # invalid_join/no_path branch — bank.sqlite's real FK graph has diameter
    # 3 (== fk_path's default max_hops), so no real table pair ever hits it.
    orphan_schema = Schema.from_sqlite("data/fintech/bank.sqlite")
    orphan_schema.tables["orphan"] = {"orphan_id"}
    orphan_schema.primary_keys["orphan"] = ["orphan_id"]
    orphan_schema.graph["orphan"] = set()

    print("\n=== verify(): full test suite, every failure kind ===")
    TEST_CASES = [
        ("valid: single table", "SELECT client_id, gender FROM client",
         True, set(), None),
        ("valid: FK join", "SELECT * FROM disp d JOIN account a ON d.account_id = a.account_id",
         True, set(), None),
        ("missing_table", "SELECT * FROM customers",
         False, {"missing_table"}, None),
        ("missing_column (qualified)", "SELECT c.age FROM client c",
         False, {"missing_column"}, None),
        ("missing_column (suggests another table)", "SELECT amount FROM client",
         False, {"missing_column"}, None),
        ("ambiguous_column",
         "SELECT type FROM card JOIN disp ON card.disp_id = disp.disp_id",
         False, {"ambiguous_column"}, None),
        ("invalid_join: wrong_columns",
         "SELECT * FROM disp d JOIN account a ON d.disp_id = a.account_id",
         False, {"invalid_join"}, None),
        ("invalid_join: needs_bridge",
         "SELECT * FROM client c JOIN loan l ON c.client_id = l.account_id",
         False, {"invalid_join"}, None),
        ("invalid_join: no_path",
         "SELECT * FROM client c JOIN orphan o ON c.client_id = o.orphan_id",
         False, {"invalid_join"}, orphan_schema),
        ("cartesian_join (warning only, ok stays True)", "SELECT * FROM client, loan",
         True, {"cartesian_join"}, None),
        ("reserved_word (warning only, ok stays True)", "SELECT * FROM order",
         True, {"reserved_word"}, None),
        ("parse_error", "SELECT FROM WHERE",
         False, {"parse_error"}, None),
    ]

    passed = 0
    for label, sql, expected_ok, expected_kinds, schema_override in TEST_CASES:
        result = verify(sql, schema_override or schema)
        actual_kinds = {i.kind for i in result.issues}
        success = result.ok == expected_ok and actual_kinds == expected_kinds
        passed += int(success)

        print(f"\n[{'PASS' if success else 'FAIL'}] {label}")
        print(f"  sql: {sql}")
        print(f"  {result.feedback()}")
        if not success:
            print(
                f"  ** expected ok={expected_ok} kinds={expected_kinds}, "
                f"got ok={result.ok} kinds={actual_kinds}"
            )

    print(f"\n{passed}/{len(TEST_CASES)} passed")
