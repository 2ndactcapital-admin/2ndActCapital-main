"use client";

// UDFUI1 — who may see / edit each custom tab and field. The engine binds both to PROFILES and PERMISSION SETS (not
// roles): a tab is visible / hidden, a field hidden / read-only / editable; most restrictive wins across the two paths;
// no grant = visible / editable. "Preview as" calls GET /udf/preview — the EXISTING resolve_tab_visibility /
// resolve_field_access_bulk for a chosen person — and shows its answer verbatim. The profile / permission-set / people
// lists come from their own admin routes (their own gates); a refused list leaves the picker unavailable, never guessed.
import { useEffect, useState } from "react";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { canWrite, describeGrants, isSuperAdmin, grantBody, label, previewRows } from "@/lib/udfBuilder.mjs";
import { Card, Notice, Select, btnCls, primaryCls, udfCall } from "./udfParts";

function GrantEditor({ kind, subject, envelope, profiles, permissionSets, initialGrants }) {
  // a platform field's grants are written under the Super-Admin scope only (the engine's _lifecycle_write_scope)
  const write = canWrite(envelope) && (subject?.owner_scope !== "platform" || isSuperAdmin(envelope));
  const [grants, setGrants] = useState(initialGrants || null);
  const [form, setForm] = useState({ granteeKind: "profile", granteeId: "", value: kind === "tab" ? "false" : "read" });
  const [error, setError] = useState(null);
  const busy = useBusy();
  const listAction = kind === "tab" ? "tabGrants" : "fieldGrants";
  const setAction = kind === "tab" ? "setTabGrant" : "setFieldGrant";

  async function load() {
    try { setGrants((await udfCall(listAction, { id: subject.id })).rows || []); setError(null); } catch (e) { setError(e.message); }
  }
  useEffect(() => { if (!initialGrants) load(); }, [subject.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickList = form.granteeKind === "profile" ? profiles : permissionSets;
  const described = describeGrants(grants, profiles, permissionSets, kind);
  const valueLabel = (v) => (kind === "tab" ? (v ? "Visible" : "Hidden") : label(envelope, "access", v));
  const valueOptions = kind === "tab"
    ? [{ value: "true", label: "Visible" }, { value: "false", label: "Hidden" }]
    : (envelope?.vocabularies?.access_levels || []).map((a) => ({ value: a, label: label(envelope, "access", a) }));

  return (
    <div className="mt-3" data-grant-editor={kind}>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {grants && described.length === 0 ? <Notice>No grants — {kind === "tab" ? "visible" : "editable"} for everyone who can open the record.</Notice> : null}
      {described.length ? (
        <table className="w-full text-sm" aria-label={`Grants on ${subject.label}`}>
          <thead><tr className="text-left text-text-muted"><th scope="col">Granted to</th><th scope="col">Kind</th><th scope="col">Access</th></tr></thead>
          <tbody>
            {described.map((g) => (
              <tr key={`${g.kind}-${g.id}`} className="border-t border-border">
                <td>{g.name}</td><td>{g.kind === "profile" ? "Profile" : "Permission set"}</td><td>{valueLabel(g.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {write ? (
        Array.isArray(pickList) ? (
          <form className="mt-2 grid grid-cols-1 items-end gap-x-3 md:grid-cols-4" aria-label={`Set access on ${subject.label}`}
            onSubmit={(e) => {
              e.preventDefault();
              if (!form.granteeId) { setError("Choose who this applies to."); return; }
              const value = kind === "tab" ? form.value === "true" : form.value;
              busy.run(async () => {
                await udfCall(setAction, { id: subject.id }, { body: grantBody(form.granteeKind, form.granteeId, value, kind) });
                await load();
              });
            }}>
            <Select id={`udf-g-kind-${subject.id}`} label="Applies to" value={form.granteeKind}
              onChange={(v) => setForm((f) => ({ ...f, granteeKind: v, granteeId: "" }))}
              options={[{ value: "profile", label: "Profile" }, { value: "permission_set", label: "Permission set" }]} />
            <Select id={`udf-g-who-${subject.id}`} label={form.granteeKind === "profile" ? "Profile" : "Permission set"}
              value={form.granteeId} placeholder="Choose" onChange={(v) => setForm((f) => ({ ...f, granteeId: v }))}
              options={pickList.map((p) => ({ value: String(p.id), label: p.name }))} />
            <Select id={`udf-g-val-${subject.id}`} label="Access" value={form.value}
              onChange={(v) => setForm((f) => ({ ...f, value: v }))} options={valueOptions} />
            <div className="mb-3"><BusyButton type="submit" busy={busy.busy} className={primaryCls} data-action={setAction}>Set access</BusyButton>
              <BusyError error={busy.error} /></div>
          </form>
        ) : <Notice>Choosing a {form.granteeKind === "profile" ? "profile" : "permission set"} needs the Manage organization settings permission.</Notice>
      ) : null}
    </div>
  );
}

export function PreviewAs({ envelope, users, targets, initialPreview }) {
  const [userId, setUserId] = useState("");
  const [target, setTarget] = useState(targets.includes("entity") ? "entity" : targets[0] || "");
  const [preview, setPreview] = useState(initialPreview || null);
  const busy = useBusy();
  const rows = previewRows(preview, envelope);
  return (
    <Card title="Preview as">
      {Array.isArray(users) ? (
        <form className="grid grid-cols-1 items-end gap-x-3 md:grid-cols-3" aria-label="Preview access as a person"
          onSubmit={(e) => {
            e.preventDefault();
            if (!userId) return;
            busy.run(async () => setPreview(await udfCall("preview", {}, { query: { user_id: userId, target_type: target } })));
          }}>
          <Select id="udf-pv-user" label="Person" value={userId} placeholder="Choose a person" onChange={setUserId}
            options={users.map((u) => ({ value: String(u.id), label: u.full_name ? `${u.full_name} (${u.email || ""})` : u.email || String(u.id) }))} />
          <Select id="udf-pv-target" label="Record type" value={target} onChange={setTarget}
            options={targets.map((k) => ({ value: k, label: label(envelope, "applies_to", k) }))} />
          <div className="mb-3"><BusyButton type="submit" busy={busy.busy} className={btnCls} data-action="preview">Preview</BusyButton>
            <BusyError error={busy.error} /></div>
        </form>
      ) : <Notice>Choosing a person needs the Manage members permission.</Notice>}
      {preview ? (
        <div data-preview="1">
          <h3 className="mt-2 text-sm font-semibold text-navy">Tabs</h3>
          {(preview.tabs || []).length === 0 ? <Notice>No custom tabs.</Notice> : (
            <ul className="text-sm">{preview.tabs.map((t) => (
              <li key={t.tab_id} data-preview-tab={t.tab_id}>{t.label}: {t.visible ? "Visible" : "Hidden"}</li>
            ))}</ul>
          )}
          <h3 className="mt-2 text-sm font-semibold text-navy">Fields</h3>
          <table className="w-full text-sm" aria-label="Field access for this person">
            <thead><tr className="text-left text-text-muted"><th scope="col">Field</th><th scope="col">Access</th><th scope="col">On its tabs</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.definition_id} className="border-t border-border" data-preview-field={r.definition_id} data-access={r.access}>
                  <td>{r.label}</td><td>{r.accessLabel}</td>
                  <td>{r.onTabs.map((t) => `${t.tab}: ${t.accessLabel}`).join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
}

export default function PermissionsPanel({ defsEnvelope, tabsEnvelope, profiles, permissionSets, users }) {
  const defs = (defsEnvelope?.rows || []).filter((d) => d.status !== "deleted");
  const tabs = (tabsEnvelope?.rows || []).filter((t) => t.status !== "deleted");
  const targets = Array.isArray(defsEnvelope?.vocabularies?.applies_to) ? defsEnvelope.vocabularies.applies_to : [];
  const [choice, setChoice] = useState("");
  const [kind, id] = choice.split(":");
  const subject = kind === "tab" ? tabs.find((t) => t.id === id) : kind === "field" ? defs.find((d) => d.id === id) : null;
  return (
    <>
      <Card title="Who may see and edit">
        <Select id="udf-perm-subject" label="Tab or field" value={choice} placeholder="Choose a tab or field" onChange={setChoice}
          options={[
            ...tabs.map((t) => ({ value: `tab:${t.id}`, label: `Tab — ${t.label} (${label(defsEnvelope, "applies_to", t.applies_to)})` })),
            ...defs.map((d) => ({ value: `field:${d.id}`, label: `Field — ${d.label} (${label(defsEnvelope, "applies_to", d.applies_to)})` })),
          ]} />
        {subject ? (
          <GrantEditor key={choice} kind={kind} subject={subject} envelope={kind === "tab" ? { ...tabsEnvelope, vocabularies: { ...tabsEnvelope?.vocabularies, access_levels: defsEnvelope?.vocabularies?.access_levels } } : defsEnvelope}
            profiles={profiles} permissionSets={permissionSets} />
        ) : null}
      </Card>
      <PreviewAs envelope={defsEnvelope} users={users} targets={targets} />
    </>
  );
}

export { GrantEditor };
