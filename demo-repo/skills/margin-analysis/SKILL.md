---
name: margin-analysis
description: Compute gross margin by product line.
---

# margin-analysis

Join order details against the product dimension to get unit cost.

```sql
SELECT p.product_line,
       SUM(o.line_total - (o.quantity * p.unit_cost)) AS gross_margin
FROM analytics.order_details o
JOIN analytics.products p ON p.product_id = o.product_id
GROUP BY 1
```

Margin is reported pre-tax. Flag any product line with negative margin.

