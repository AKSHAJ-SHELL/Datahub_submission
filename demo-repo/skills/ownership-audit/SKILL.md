---
name: ownership-audit
description: Find warehouse tables with no owner and propose one from query history.
---

# ownership-audit

Use the DataHub search tool to list datasets missing ownership, then use
get_dataset_queries to see who actually queries them.

Focus on analytics.order_details and analytics.customers first - they are the
most depended-on tables in the warehouse.

