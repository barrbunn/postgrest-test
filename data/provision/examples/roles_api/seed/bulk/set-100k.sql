-- Bulk seed set 100k: 100,000 users / 5,000 applications / 500,000 grants.
-- Prerequisites: schemas applied and seed/roles.sql run (role ids 1-4).
-- The three bulk sets occupy the same id ranges - pick ONE set.
-- Idempotent per row (ON CONFLICT DO NOTHING).

INSERT INTO users (user_id, user_name, user_email, status)
SELECT i, 'user_' || i, 'user_' || i || '@example.com',
       CASE WHEN i % 10 = 0 THEN 'disabled' ELSE 'active' END
FROM generate_series(1, 100000) AS i
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO applications (application_id, application_name, description, owner_team)
SELECT i, 'app_' || i, 'Bulk application ' || i,
       CASE WHEN i % 2 = 0 THEN 'ops' ELSE 'finance' END
FROM generate_series(1, 5000) AS i
ON CONFLICT (application_id) DO NOTHING;

INSERT INTO grants (grant_id, user_id, application_id, role_id, granted_by, granted_at)
SELECT i,
       (i % 100000) + 1,
       (i % 5000) + 1,
       (i % 4) + 1,
       'bulk-seed',
       DATE '2026-01-01' + (i % 365)
FROM generate_series(1, 500000) AS i
ON CONFLICT (grant_id) DO NOTHING;
