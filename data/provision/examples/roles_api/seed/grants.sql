-- Seed data for the roles_api example (data/provision/examples/roles_api).
-- References the ids seeded by roles.sql, users.sql and applications.sql.
INSERT INTO grants (grant_id, user_id, application_id, role_id, granted_by, granted_at) VALUES
  (1, 1, 1, 1, 'admin-team', '2026-09-01'),
  (2, 2, 2, 3, 'admin-team', '2026-09-02'),
  (3, 3, 1, 2, 'admin-team', '2026-09-03')
ON CONFLICT (grant_id) DO NOTHING;
