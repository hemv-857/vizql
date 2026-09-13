-- Complex join query for testing
WITH user_events AS (
  SELECT user_id, event_type, timestamp
  FROM events
  WHERE timestamp > '2024-01-01'
    AND event_type IN ('purchase', 'signup', 'login')
),
user_stats AS (
  SELECT
    u.id,
    u.name,
    u.email,
    u.created_at,
    COUNT(e.event_type) as event_count,
    MAX(e.timestamp) as last_event
  FROM users u
  LEFT JOIN user_events e ON u.id = e.user_id
  WHERE u.status = 'active'
  GROUP BY u.id, u.name, u.email, u.created_at
  HAVING COUNT(e.event_type) > 10
)
SELECT
  us.name,
  us.email,
  us.event_count,
  us.last_event,
  o.total_amount
FROM user_stats us
LEFT JOIN (
  SELECT user_id, SUM(amount) as total_amount
  FROM orders
  WHERE created_at > '2024-01-01'
  GROUP BY user_id
) o ON us.id = o.user_id
WHERE us.event_count > 100
ORDER BY us.event_count DESC, us.last_event DESC
LIMIT 100;