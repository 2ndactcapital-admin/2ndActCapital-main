-- udf01a — the tag-minting permission.
--
-- The sprint prompt asks for a `tag.create` permission AND says "do not invent
-- new permission strings". Those conflict: public.permissions held 28 rows and
-- no tags resource. Gating mint on the nearest existing grant (manage_portfolio)
-- would make minting and assigning the same gate, and the assertion "mint
-- without tag.create is rejected, with it succeeds" could never distinguish the
-- two — it would pass vacuously. So this adds ONE real row, following the
-- table's own {action}_{resource} naming convention (manage_portfolio,
-- view_workflow_runs, …).
--
-- Deliberately granted to NO role here. A tag vocabulary is an org-level
-- decision and the grant is an explicit admin act; auto-granting it to every
-- role that happens to hold manage_portfolio would defeat the point of
-- separating it. rbac.has_permission's zero-roles default-allow still applies
-- to single-admin orgs, exactly as it does for every other permission.

INSERT INTO public.permissions (name, resource, action)
VALUES ('create_tags', 'tags', 'create')
ON CONFLICT (resource, action) DO NOTHING;
