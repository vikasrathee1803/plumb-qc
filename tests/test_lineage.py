"""Relation-level lineage graph extraction."""

import pytest

from plumb.checks._sql import SqlParseError
from plumb.engine.lineage import build_lineage


def _by_id(g):
    return {n.id: n for n in g.nodes}


def test_simple_select_one_table():
    g = build_lineage("SELECT a, b FROM db.sch.t")
    kinds = {n.kind for n in g.nodes}
    assert "table" in kinds and "output" in kinds
    edge = next(e for e in g.edges if e.target == "output")
    assert edge.relation == "from"
    assert g.risks == []


def test_cte_and_join_graph():
    sql = (
        "WITH cust AS (SELECT id, region FROM raw.customers), "
        "ord AS (SELECT id, cust_id, amount FROM raw.orders) "
        "SELECT c.region, SUM(o.amount) FROM ord o "
        "JOIN cust c ON o.cust_id = c.id"
    )
    g = build_lineage(sql)
    nodes = _by_id(g)
    assert "cte:cust" in nodes and "cte:ord" in nodes and "output" in nodes
    assert "table:raw.customers" in nodes and "table:raw.orders" in nodes
    # CTEs feed output; tables feed the CTEs
    assert any(e.source == "cte:ord" and e.target == "output" for e in g.edges)
    assert any(e.source == "table:raw.orders" and e.target == "cte:ord" for e in g.edges)
    join_edge = next(e for e in g.edges if e.source == "cte:cust" and e.target == "output")
    assert join_edge.relation == "inner join"
    assert "cust_id" in (join_edge.on or "")
    assert g.risks == []


def test_cross_join_is_flagged_as_risk():
    g = build_lineage("SELECT a FROM t, u")
    risky = [e for e in g.edges if e.risk]
    assert len(risky) == 1
    assert any("fan-out" in r for r in g.risks)


def test_explicit_cross_join_flagged():
    g = build_lineage("SELECT a FROM t CROSS JOIN u")
    assert any(e.relation == "cross" and e.risk for e in g.edges)


def test_left_join_relation_label():
    g = build_lineage("SELECT a FROM t LEFT JOIN u ON t.id = u.id")
    e = next(e for e in g.edges if e.relation != "from")
    assert e.relation == "left join"
    assert not e.risk


def test_select_star_flag():
    g = build_lineage("SELECT * FROM t")
    out = _by_id(g)["output"]
    assert "SELECT *" in out.flags


def test_subquery_becomes_a_node():
    g = build_lineage("SELECT a FROM (SELECT a FROM base) x")
    kinds = [n.kind for n in g.nodes]
    assert "subquery" in kinds
    assert "table:base" in _by_id(g)


def test_union_pulls_both_arms():
    g = build_lineage("SELECT a FROM t1 UNION ALL SELECT a FROM t2")
    nodes = _by_id(g)
    assert "table:t1" in nodes and "table:t2" in nodes
    # both arms feed the result
    assert any(e.source == "table:t1" and e.target == "output" for e in g.edges)
    assert any(e.source == "table:t2" and e.target == "output" for e in g.edges)


def test_union_inside_a_cte():
    sql = "WITH u AS (SELECT a FROM t1 UNION SELECT a FROM t2) SELECT * FROM u"
    g = build_lineage(sql)
    nodes = _by_id(g)
    assert "cte:u" in nodes
    assert any(e.source == "table:t1" and e.target == "cte:u" for e in g.edges)
    assert any(e.source == "table:t2" and e.target == "cte:u" for e in g.edges)


def test_nested_subquery_in_join():
    g = build_lineage("SELECT a FROM base b JOIN (SELECT id FROM inner_t) s ON b.id = s.id")
    nodes = _by_id(g)
    assert "table:base" in nodes and "table:inner_t" in nodes
    assert any(n.kind == "subquery" for n in g.nodes)


def test_unparseable_sql_raises():
    with pytest.raises(SqlParseError):
        build_lineage("SELEKT FROM WHERE )(")


def test_deeply_nested_subqueries_do_not_crash():
    # 80 levels of nesting must not raise RecursionError; depth-bounded.
    sql = "SELECT a FROM base"
    for _ in range(80):
        sql = f"SELECT a FROM ({sql}) x"
    g = build_lineage(sql)  # should return, bounded, not blow the stack
    assert any(n.kind == "output" for n in g.nodes)


def test_recursive_cte_has_no_self_loop():
    sql = (
        "WITH RECURSIVE nums AS ("
        "SELECT 1 AS n UNION ALL SELECT n + 1 FROM nums WHERE n < 10"
        ") SELECT n FROM nums"
    )
    g = build_lineage(sql)
    assert not any(e.source == e.target for e in g.edges)


def test_graph_serializes_to_contract():
    g = build_lineage("SELECT a FROM t JOIN u ON t.id = u.id")
    dumped = g.model_dump(mode="json")
    assert "nodes" in dumped and "edges" in dumped and "risks" in dumped
