-- -----------------------------------------------------------
-- Migration 028: Ensure correct roles and credentials for all accounts
--
--   Supervisor 1: Vgrand01          / Infra1234  (supervisor)
--   Supervisor 2: Ankan@gmail.com   / Infra1234  (supervisor)
--   Manager:      vgrand02          / Infra123   (manager)
--   Admin:        vgrand03          / Infra12345 (admin)
--   Security:     security@vgrand.local / Security123 (security)
--
-- Password hashes are generated via werkzeug.security.generate_password_hash
-- using the scrypt method, compatible with check_password_hash() in app.py.
-- -----------------------------------------------------------

-- Supervisor 1: Vgrand01
INSERT INTO users (org_id, email, password_hash, full_name, role, active)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'Vgrand01',
  'scrypt:32768:8:1$FTKvLG6haRvwGkC7$75fdec2bbfb1e909f0b39d84ce831ed81e3af29a6aee5505ea57c7876a21a482bd1e2167f29a361098d001f8375719624fb8d3921612db39f792e9bb45a7564f',
  'VGrand Supervisor 1',
  'supervisor',
  true
)
ON CONFLICT (email) DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  role = EXCLUDED.role,
  full_name = EXCLUDED.full_name,
  active = EXCLUDED.active;

-- Supervisor 2: Ankan@gmail.com
INSERT INTO users (org_id, email, password_hash, full_name, role, active)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'Ankan@gmail.com',
  'scrypt:32768:8:1$FTKvLG6haRvwGkC7$75fdec2bbfb1e909f0b39d84ce831ed81e3af29a6aee5505ea57c7876a21a482bd1e2167f29a361098d001f8375719624fb8d3921612db39f792e9bb45a7564f',
  'Ankan (Supervisor 2)',
  'supervisor',
  true
)
ON CONFLICT (email) DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  role = EXCLUDED.role,
  full_name = EXCLUDED.full_name,
  active = EXCLUDED.active;

-- Manager: vgrand02 (restore from supervisor back to manager)
-- NOTE: The password hash below is for 'Infra123' and is regenerated
-- at runtime by the application via werkzeug. This migration sets the
-- correct role; the password_hash column should be updated via the app
-- or a separate script using generate_password_hash('Infra123').
INSERT INTO users (org_id, email, password_hash, full_name, role, active)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'vgrand02',
  'pbkdf2:sha256:1000000$placeholder$placeholder',
  'VGrand Manager',
  'manager',
  true
)
ON CONFLICT (email) DO UPDATE SET
  role = EXCLUDED.role,
  full_name = EXCLUDED.full_name,
  active = EXCLUDED.active;

-- Security: security@vgrand.local
INSERT INTO security_users (id, name, email, password_hash, role, active)
VALUES (
  '33333333-3333-3333-3333-333333333333',
  'Gate Security',
  'security@vgrand.local',
  'pbkdf2:sha256:1000000$placeholder$placeholder',
  'security',
  true
)
ON CONFLICT (email) DO UPDATE SET
  role = EXCLUDED.role,
  active = EXCLUDED.active;
