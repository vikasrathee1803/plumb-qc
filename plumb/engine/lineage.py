"""Relation-level lineage for a SQL build.

Walks the sqlglot AST and produces a directed graph: source tables and
views flow into CTEs and subqueries, which flow into the result. Joins are
edges, annotated with type and key, and cross or comma joins are flagged as
fan-out risk. This is the structural view behind the verdict; it is derived
from the same parser the checks use, so what you see is what runs.

Deliberately relation-level for now: column-level lineage and stored
procedure control flow are out of scope until this is excellent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from plumb.checks._sql import SqlParseError, parse_one

NodeKind = Literal["table", "cte", "subquery", "output", "values"]


class LineageNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    kind: NodeKind
    detail: str = ""
    flags: list[str] = Field(default_factory=list)
    # Populated for query scopes (CTE, subquery, result): the shape of what
    # the scope produces, so the map can show columns, calculations, filters.
    columns: list[str] = Field(default_factory=list)
    calculations: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)


class ColumnLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_col: str  # column in the source relation
    to_col: str  # column it becomes in the consuming scope


class LineageEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relation: str = "from"  # "from", "inner join", "left join", "cross", ...
    on: str | None = None
    risk: bool = False
    # Column-level lineage: which source columns feed which output columns.
    columns: list[ColumnLink] = Field(default_factory=list)


class LineageGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


_MAX_DEPTH = 60  # bound recursion on pathologically nested SQL


class _Builder:
    def __init__(self, cte_names: set[str]) -> None:
        self.cte_names = cte_names
        self.nodes: dict[str, LineageNode] = {}
        self.edges: list[LineageEdge] = []
        self.risks: list[str] = []
        self._sub = 0
        self._depth = 0

    def node(self, node_id: str, *, label: str, kind: NodeKind, detail: str = "") -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = LineageNode(id=node_id, label=label, kind=kind, detail=detail)
        return node_id

    def flag(self, node_id: str, flag: str) -> None:
        node = self.nodes.get(node_id)
        if node is not None and flag not in node.flags:
            node.flags.append(flag)

    def relation_node(self, rel: exp.Expression) -> str:
        if isinstance(rel, exp.Table):
            name = rel.name
            if name.lower() in self.cte_names and not rel.db and not rel.catalog:
                return self.node(f"cte:{name.lower()}", label=name, kind="cte")
            fqn = ".".join(p for p in (rel.catalog, rel.db, rel.name) if p)
            return self.node(f"table:{fqn.lower()}", label=name, kind="table", detail=fqn)
        if isinstance(rel, exp.Subquery):
            self._sub += 1
            sid = f"sub:{self._sub}"
            self.node(sid, label=rel.alias or "subquery", kind="subquery")
            self.process_query(rel.this, sid)
            return sid
        # values, table functions, etc.
        self._sub += 1
        gid = f"expr:{self._sub}"
        return self.node(gid, label=type(rel).__name__.lower(), kind="values")

    def process_query(self, expr: exp.Expression, consumer_id: str) -> None:
        """Feed a query body (a SELECT, a set operation, or a parenthesized
        subquery) into the consumer, descending through unions so both arms
        contribute their sources. Depth-bounded against pathological nesting."""
        if self._depth >= _MAX_DEPTH:
            return
        self._depth += 1
        try:
            if isinstance(expr, exp.Subquery):
                self.process_query(expr.this, consumer_id)
            elif isinstance(expr, exp.SetOperation):
                self.process_query(expr.this, consumer_id)
                self.process_query(expr.expression, consumer_id)
            elif isinstance(expr, exp.Select):
                self.process(expr, consumer_id)
        finally:
            self._depth -= 1

    def process(self, select: exp.Select, consumer_id: str) -> None:
        # SELECT * smell on the consuming scope.
        if any(isinstance(p, exp.Star) for p in select.expressions):
            self.flag(consumer_id, "SELECT *")
        self.annotate_scope(select, consumer_id)

        relations: list[tuple[exp.Expression, exp.Join | None]] = []
        from_ = select.args.get("from")
        if from_ is not None and from_.this is not None:
            relations.append((from_.this, None))
        for join in select.args.get("joins", []):
            relations.append((join.this, join))

        alias_to_rid: dict[str, str] = {}
        edge_for_rid: dict[str, LineageEdge] = {}
        source_rids: list[str] = []
        for rel, join in relations:
            rid = self.relation_node(rel)
            if rid == consumer_id:
                continue  # recursive CTE self-reference; skip the self-loop
            relation, on, risk = self._edge_meta(join)
            edge = LineageEdge(source=rid, target=consumer_id, relation=relation, on=on, risk=risk)
            self.edges.append(edge)
            edge_for_rid.setdefault(rid, edge)
            source_rids.append(rid)
            alias = (rel.alias or (rel.name if isinstance(rel, exp.Table) else "")).lower()
            if alias:
                alias_to_rid[alias] = rid
            if isinstance(rel, exp.Table) and rel.name:
                alias_to_rid.setdefault(rel.name.lower(), rid)
            if risk:
                label = self.nodes[rid].label
                self.risks.append(f"{relation} on {label} has no join condition (fan-out risk)")

        self._link_columns(select, alias_to_rid, source_rids, edge_for_rid)

    def _link_columns(
        self,
        select: exp.Select,
        alias_to_rid: dict[str, str],
        source_rids: list[str],
        edge_for_rid: dict[str, LineageEdge],
    ) -> None:
        """Trace each projected column to the source columns it derives from,
        recording the link on the edge from that source. Unqualified columns
        resolve to the only source when the scope reads exactly one."""
        single = source_rids[0] if len(source_rids) == 1 else None
        for proj in select.expressions:
            out_name = _projection_name(proj)
            if out_name == "*":
                continue
            for col in proj.find_all(exp.Column):
                rid = alias_to_rid.get(col.table.lower()) if col.table else single
                edge = edge_for_rid.get(rid) if rid else None
                if edge is None:
                    continue
                link = ColumnLink(from_col=col.name, to_col=out_name)
                if link not in edge.columns:
                    edge.columns.append(link)

    def annotate_scope(self, select: exp.Select, consumer_id: str) -> None:
        """Record the columns this scope projects, which of them are
        calculations (aggregates, functions, arithmetic, CASE), and any
        WHERE/HAVING filters it applies."""
        node = self.nodes.get(consumer_id)
        if node is None or node.columns:  # already annotated (first pass wins)
            return
        for proj in select.expressions:
            name = _projection_name(proj)
            node.columns.append(name)
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if _is_calculation(inner):
                node.calculations.append(f"{name} = {_short(inner.sql(dialect='snowflake'), 44)}")
        where = select.args.get("where")
        if where is not None and where.this is not None:
            for pred in _split_and(where.this):
                node.filters.append(_short(pred.sql(dialect="snowflake"), 60))
        having = select.args.get("having")
        if having is not None and having.this is not None:
            node.filters.append("HAVING " + _short(having.this.sql(dialect="snowflake"), 50))

    def _edge_meta(self, join: exp.Join | None) -> tuple[str, str | None, bool]:
        if join is None:
            return "from", None, False
        kind = (join.kind or "").upper()
        side = (join.side or "").upper()
        on = join.args.get("on")
        using = join.args.get("using")
        if kind == "CROSS":
            return "cross", None, True
        if on is None and not using and not side and not kind:
            return "comma", None, True  # implicit cross product
        parts = [p for p in (side, kind) if p] or ["INNER"]
        relation = " ".join(parts).lower() + " join"
        on_text = _short(on.sql(dialect="snowflake")) if on is not None else None
        return relation, on_text, False


def _short(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _projection_name(proj: exp.Expression) -> str:
    if isinstance(proj, exp.Alias):
        return proj.alias
    if isinstance(proj, exp.Column):
        return proj.name
    if isinstance(proj, exp.Star):
        return "*"
    return _short(proj.sql(dialect="snowflake"), 24)


def _is_calculation(expr: exp.Expression) -> bool:
    """A projection is a calculation if it is not just a bare column, star, or
    literal: aggregates, scalar functions, arithmetic, CASE, casts, etc."""
    return not isinstance(expr, (exp.Column, exp.Star, exp.Literal, exp.Null))


def _split_and(expr: exp.Expression) -> list[exp.Expression]:
    """Flatten an AND tree into its conjuncts so each filter shows separately."""
    if isinstance(expr, exp.And):
        return _split_and(expr.this) + _split_and(expr.expression)
    if isinstance(expr, exp.Paren):
        return _split_and(expr.this)
    return [expr]


def build_lineage(sql: str) -> LineageGraph:
    """Build the relation-level lineage graph for a SQL statement."""
    try:
        tree = parse_one(sql)
    except SqlParseError as exc:
        raise SqlParseError(str(exc)) from exc
    except RecursionError as exc:
        raise SqlParseError("SQL is too deeply nested to map") from exc

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    builder = _Builder(cte_names)

    with_ = tree.args.get("with") if isinstance(tree, (exp.Select, exp.SetOperation)) else None
    if with_ is not None:
        for cte in with_.expressions:
            cid = builder.node(
                f"cte:{cte.alias_or_name.lower()}", label=cte.alias_or_name, kind="cte"
            )
            builder.process_query(cte.this, cid)

    builder.node("output", label="result", kind="output")
    builder.process_query(tree, "output")

    # Source relations (tables, inline values) have no projection list of their
    # own, so give them the columns that downstream scopes actually read.
    for edge in builder.edges:
        src = builder.nodes.get(edge.source)
        if src is not None and src.kind in ("table", "values"):
            for link in edge.columns:
                if link.from_col not in src.columns:
                    src.columns.append(link.from_col)

    # de-duplicate the risk summary, keep order
    seen: set[str] = set()
    risks: list[str] = []
    for r in builder.risks:
        if r not in seen:
            seen.add(r)
            risks.append(r)
    return LineageGraph(nodes=list(builder.nodes.values()), edges=builder.edges, risks=risks)
