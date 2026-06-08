"""The complex demo query over the real PORTFOLIO_DEMO_DB views, plus a
check that it maps into a rich lineage graph with a fan-out risk."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.engine.lineage import build_lineage  # noqa: E402

DEMO_SQL = """\
WITH regional_orders AS (
    SELECT customer_region, customer_segment,
           SUM(total_revenue) AS region_revenue, SUM(order_count) AS orders
    FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_ORDER_ANALYTICS
    WHERE total_revenue > 0
    GROUP BY customer_region, customer_segment
),
customer_value AS (
    SELECT region, segment, COUNT(*) AS customers, SUM(lifetime_revenue) AS ltv
    FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV
    WHERE lifetime_revenue > 0
    GROUP BY region, segment
),
supplier_health AS (
    SELECT supplier_region, AVG(late_delivery_pct) AS avg_late
    FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_SUPPLIER_PERFORMANCE
    GROUP BY supplier_region
),
brand_margin AS (
    SELECT supplier_nation, SUM(gross_margin) AS margin
    FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_PRODUCT_MARGIN
    GROUP BY supplier_nation
)
SELECT ro.customer_region, ro.customer_segment, ro.region_revenue,
       cv.ltv, cv.customers, sh.avg_late, bm.margin
FROM regional_orders ro
JOIN customer_value cv
  ON ro.customer_region = cv.region AND ro.customer_segment = cv.segment
LEFT JOIN supplier_health sh ON ro.customer_region = sh.supplier_region,
     brand_margin bm"""

if __name__ == "__main__":
    g = build_lineage(DEMO_SQL)
    print("nodes:", len(g.nodes), "| edges:", len(g.edges))
    for n in g.nodes:
        print(f"  {n.kind:9} {n.label}  flags={n.flags}")
    print("edges:")
    for e in g.edges:
        print(f"  {e.source} -> {e.target}  [{e.relation}] risk={e.risk}")
    print("risks:", g.risks)
