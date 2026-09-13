-- Deeply nested CTEs
WITH
raw_events AS (
  SELECT * FROM events WHERE timestamp > '2024-01-01'
),
filtered_events AS (
  SELECT * FROM raw_events WHERE event_type != 'heartbeat'
),
enriched_events AS (
  SELECT
    e.*,
    u.name as user_name,
    u.plan as user_plan
  FROM filtered_events e
  JOIN users u ON e.user_id = u.id
),
daily_agg AS (
  SELECT
    DATE(timestamp) as day,
    user_plan,
    COUNT(*) as event_count,
    COUNT(DISTINCT user_id) as unique_users
  FROM enriched_events
  GROUP BY DATE(timestamp), user_plan
),
weekly_agg AS (
  SELECT
    DATE_TRUNC('week', day) as week,
    user_plan,
    SUM(event_count) as total_events,
    AVG(unique_users) as avg_unique_users
  FROM daily_agg
  GROUP BY DATE_TRUNC('week', day), user_plan
),
monthly_agg AS (
  SELECT
    DATE_TRUNC('month', week) as month,
    user_plan,
    SUM(total_events) as monthly_events,
    AVG(avg_unique_users) as monthly_avg_users
  FROM weekly_agg
  GROUP BY DATE_TRUNC('month', week), user_plan
),
final_stats AS (
  SELECT
    month,
    user_plan,
    monthly_events,
    monthly_avg_users,
    LAG(monthly_events) OVER (PARTITION BY user_plan ORDER BY month) as prev_month_events
  FROM monthly_agg
)
SELECT
  month,
  user_plan,
  monthly_events,
  CASE
    WHEN prev_month_events > 0
    THEN ROUND(100.0 * (monthly_events - prev_month_events) / prev_month_events, 2)
    ELSE NULL
  END as mom_growth_pct
FROM final_stats
WHERE month >= '2024-01-01'
ORDER BY month DESC, user_plan;