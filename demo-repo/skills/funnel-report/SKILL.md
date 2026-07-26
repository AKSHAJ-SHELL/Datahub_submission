---
name: funnel-report
description: Build a signup-to-purchase funnel for a date range.
---

# funnel-report

Read from the events table and the customer dimension.

```sql
SELECT e.step, COUNT(DISTINCT e.customer_id) AS users
FROM analytics.events e
JOIN analytics.customers c ON c.customer_id = e.customer_id
GROUP BY 1
```

