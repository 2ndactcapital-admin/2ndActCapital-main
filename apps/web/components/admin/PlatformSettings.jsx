"use client";

import { useEffect, useState } from "react";

import OrgSettingsEditor from "@/components/admin/OrgSettingsEditor";

/**
 * Sprint 24 — Super Admin platform screen.
 *
 * Lists every tenant org, lets one be selected for editing, and onboards a new
 * client. Gated server-side by is_super_admin; the API re-checks every call.
 */
export default function PlatformSettings() {
  const [orgs, setOrgs] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [createError, setCreateError] = useState(null);

  async function loadOrgs(selectId) {
    try {
      const res = await fetch("/api/orgs", { cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Could not load organizations");
      setOrgs(body.orgs || []);
      setSelected((cur) => selectId || cur || body.orgs?.[0]?.id || null);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    loadOrgs();
  }, []);

  async function createOrg(e) {
    e.preventDefault();
    setCreateError(null);
    try {
      const res = await fetch("/api/orgs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Could not create organization");
      setName("");
      setSlug("");
      setCreating(false);
      // Jump straight to the new org so branding can be configured now.
      await loadOrgs(body.id);
    } catch (e) {
      setCreateError(e.message);
    }
  }

  if (error) {
    return (
      <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        {error}
      </div>
    );
  }

  if (!orgs) {
    return (
      <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        Loading organizations…
      </div>
    );
  }

  const current = orgs.find((o) => o.id === selected);

  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-text-muted" htmlFor="org-select">
          Organization
        </label>
        <select
          id="org-select"
          value={selected || ""}
          onChange={(e) => setSelected(e.target.value)}
          className="rounded border px-3 py-1.5 text-sm"
          style={{
            borderColor: "var(--2a-border)",
            background: "var(--2a-bg-card)",
          }}
        >
          {orgs.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name} ({o.slug})
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={() => setCreating((c) => !c)}
          className="rounded border px-3 py-1.5 text-sm transition-colors"
          style={{
            borderColor: "var(--2a-gold)",
            color: "var(--2a-navy)",
            background: "transparent",
          }}
        >
          {creating ? "Cancel" : "Create organization"}
        </button>
      </div>

      {creating && (
        <form
          onSubmit={createOrg}
          className="mt-4 rounded-md border border-border bg-bg-card p-4"
        >
          <p className="text-sm text-text-muted">
            Onboarding a new client starts here. The org renders with default
            branding until its Org Admin configures it.
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs text-text-muted" htmlFor="org-name">
                Name
              </label>
              <input
                id="org-name"
                value={name}
                required
                onChange={(e) => setName(e.target.value)}
                className="mt-1 rounded border px-3 py-1.5 text-sm"
                style={{
                  borderColor: "var(--2a-border)",
                  background: "var(--2a-bg-card)",
                }}
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted" htmlFor="org-slug">
                Slug
              </label>
              <input
                id="org-slug"
                value={slug}
                required
                placeholder="acme-partners"
                onChange={(e) => setSlug(e.target.value)}
                className="mt-1 rounded border px-3 py-1.5 text-sm"
                style={{
                  borderColor: "var(--2a-border)",
                  background: "var(--2a-bg-card)",
                  fontFamily: "ui-monospace, monospace",
                }}
              />
            </div>
            <button
              type="submit"
              className="rounded border px-4 py-2 text-sm font-medium"
              style={{
                background: "var(--2a-navy)",
                color: "var(--2a-bg)",
                borderColor: "var(--2a-navy)",
              }}
            >
              Create
            </button>
          </div>
          {createError && (
            <p className="mt-2 text-sm" style={{ color: "#9B2335" }}>
              {createError}
            </p>
          )}
        </form>
      )}

      {selected && (
        <OrgSettingsEditor
          key={selected}
          orgId={selected}
          orgName={current?.name}
          canEdit
        />
      )}
    </div>
  );
}
