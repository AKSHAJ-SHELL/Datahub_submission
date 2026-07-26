---
name: revenue-lookup
description: Answer revenue questions for a period, region, or product line.
---

# revenue-lookup

Query the order details table for completed orders and sum the line totals.

```sql
SELECT
  date_trunc('day', o.created_at) AS day,
  SUM(o.line_total)               AS revenue
FROM analytics.order_details o
WHERE o.status = 'completed'
GROUP BY 1
ORDER BY 1 DESC
```

Always exclude refunded orders. If the caller does not specify a period,
default to the trailing 30 days.

