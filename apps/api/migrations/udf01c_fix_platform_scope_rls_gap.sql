-- Fixes FIND 7 from verify_udf01c.py: platform-scope FLS grants were
-- invisible to non-super-admin connections because org_id IS NULL never
-- equals current_org_id, causing resolution to silently fail open to 'edit'.
-- Also fixes the identical latent shape on udf_tab_permissions (FIND 1),
-- not currently exploitable since tabs are always org-scoped, but the same
-- gap would open the moment platform-scope tabs exist.

BEGIN;

DROP POLICY udf_field_permissions_org_isolation ON portfolio.udf_field_permissions;
CREATE POLICY udf_field_permissions_org_isolation ON portfolio.udf_field_permissions
  USING (
    definition_id IN (
      SELECT id FROM portfolio.udf_definitions
       WHERE org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
          OR org_id IS NULL
    )
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  );

DROP POLICY udf_tab_permissions_org_isolation ON portfolio.udf_tab_permissions;
CREATE POLICY udf_tab_permissions_org_isolation ON portfolio.udf_tab_permissions
  USING (
    tab_id IN (
      SELECT id FROM portfolio.udf_tabs
       WHERE org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
          OR org_id IS NULL
    )
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  );

COMMIT;
