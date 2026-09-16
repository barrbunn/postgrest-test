-- Seed data for the roles_api example (data/provision/examples/roles_api).
INSERT INTO users (user_id, user_name, user_email, status) VALUES
  (1, 'alice', 'alice@example.com', 'active'),
  (2, 'bob', 'bob@example.com', 'active'),
  (3, 'carol', 'carol@example.com', 'active'),
  (4, 'dave', 'dave@example.com', 'disabled')
ON CONFLICT (user_id) DO NOTHING;
