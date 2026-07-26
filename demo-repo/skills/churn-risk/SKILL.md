---
name: churn-risk
description: Identify customers at risk of churning in the next 30 days.
---

# churn-risk

Pull recency and frequency features from the customer table, then join
order history.

```sql
SELECT c.customer_id, MAX(o.created_at) AS last_order
FROM analytics.customers c
LEFT JOIN analytics.order_details o ON o.customer_id = c.customer_id
GROUP BY 1
```

Customers with no order in 90 days are high risk.

