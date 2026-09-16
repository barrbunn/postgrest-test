-- Seed data for the roles_api example (data/provision/examples/roles_api).
-- Idempotent: re-running skips existing rows.
-- Run order: roles.sql, users.sql, applications.sql, grants.sql (FKs).
INSERT INTO roles (role_id, role_name, description) VALUES
  (1, 'editor', 'Can edit resources'),
  (2, 'viewer', 'Read-only access'),
  (3, 'administrator', 'Full administrative access'),
  (4, 'manager', 'Manages teams and grants')
ON CONFLICT (role_id) DO NOTHING;
