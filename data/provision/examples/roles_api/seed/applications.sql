-- Seed data for the roles_api example (data/provision/examples/roles_api).
INSERT INTO applications (application_id, application_name, description, owner_team) VALUES
  (1, 'inventory', 'Warehouse inventory system', 'ops'),
  (2, 'billing', 'Billing and invoicing', 'finance')
ON CONFLICT (application_id) DO NOTHING;
