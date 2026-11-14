// UDFUI1 — the custom-fields builder's pure rules (no React, no fetch: node tests drive the real logic).
//
// EVERY control maps to ONE existing (or UDFUI1 admin-read) FastAPI route, listed in UDF_ROUTES; the browser reaches
// it only through app/api/udf/builder/[...path] (Rule 5), which forwards ONLY what this table lists. Every control is
// rendered only when the server's envelope allows it — `permissions.can_write === true`, never a truthy fallback; a
// lost envelope renders no write control at all. Labels come from the envelope's `vocabularies.labels` (Rule 1).

/** action → [method, path under /api/v1/udf/] — `:x` segments are parameters. */
export const UDF_ROUTES = Object.freeze({
  listDefinitions: ["GET", "admin/definitions"],
  createDefinition: ["POST", "definitions"],
  editDefinition: ["PATCH", "definitions/:id"],
  deleteDefinition: ["DELETE", "definitions/:id"],
  deactivateDefinition: ["POST", "definitions/:id/deactivate"],
  reactivateDefinition: ["POST", "definitions/:id/reactivate"],
  undeleteDefinition: ["POST", "definitions/:id/undelete"],
  definitionAudit: ["GET", "definitions/:id/audit"],
  listTabs: ["GET", "admin/tabs"],
  createTab: ["POST", "tabs"],
  editTab: ["PATCH", "tabs/:id"],
  deleteTab: ["DELETE", "tabs/:id"],
  deactivateTab: ["POST", "tabs/:id/deactivate"],
  reactivateTab: ["POST", "tabs/:id/reactivate"],
  undeleteTab: ["POST", "tabs/:id/undelete"],
  tabGrants: ["GET", "tabs/:id/permissions"],
  setTabGrant: ["PUT", "tabs/:id/permissions"],
  fieldGrants: ["GET", "fields/:id/permissions"],
  setFieldGrant: ["PUT", "fields/:id/permissions"],
  layout: ["GET", "layouts/:tab"],
  addSection: ["POST", "layouts/:tab/sections"],
  orderSections: ["PUT", "layouts/:tab/sections/order"],
  removeSection: ["DELETE", "layouts/:tab/sections/:section"],
  addItem: ["POST", "layouts/:tab/sections/:section/items"],
  moveItem: ["PATCH", "layouts/:tab/items/:item"],
  removeItem: ["DELETE", "layouts/:tab/items/:item"],
  tagVocabulary: ["GET", "tags/:id"],
  mergeTags: ["POST", "tags/:id/merge"],
  preview: ["GET", "preview"],
  records: ["GET", "records/:type"],
  exportRecords: ["GET", "records/:type/export"],
});

export const BUILDER_BASE = "/api/udf/builder";

function fill(template, params = {}) {
  return template.split("/").map((seg) => {
    if (!seg.startsWith(":")) return seg;
    const v = params[seg.slice(1)];
    if (v === undefined || v === null || v === "") throw new Error(`missing route parameter ${seg}`);
    return encodeURIComponent(String(v));
  }).join("/");
}

/** The FastAPI route (method + /udf/… path) an action calls. */
export function apiRoute(action, params) {
  const r = UDF_ROUTES[action];
  if (!r) throw new Error(`unknown builder action ${action}`);
  return { method: r[0], path: `/udf/${fill(r[1], params)}` };
}

/** The browser-side URL + method for an action (through the Next forwarder), with an optional query. */
export function builderRequest(action, params, query) {
  const { method, path } = apiRoute(action, params);
  let url = `${BUILDER_BASE}${path.slice("/udf".length)}`;
  const q = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (q.length) url += `?${new URLSearchParams(q).toString()}`;
  return { method, url };
}

/** Is (method, path segments under /udf/) one of the listed routes? The Next forwarder refuses anything else. */
export function isForwardable(method, segments) {
  const segs = (segments || []).map(String);
  return Object.values(UDF_ROUTES).some(([m, tpl]) => {
    if (m !== method) return false;
    const parts = tpl.split("/");
    if (parts.length !== segs.length) return false;
    return parts.every((p, i) => (p.startsWith(":") ? segs[i] !== "" && !segs[i].includes("/") : p === segs[i]));
  });
}

/** A request body with any org_id removed (org scope is the session's, never the client's — Rule 6). */
export function stripOrg(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return body;
  const out = {};
  for (const [k, v] of Object.entries(body)) if (k !== "org_id") out[k] = v;
  return out;
}

// ── envelope → controls (fail closed) ─────────────────────────────────────────────────────────────────────────

export function canWrite(envelope) {
  return envelope?.permissions?.can_write === true;
}

export function isSuperAdmin(envelope) {
  return envelope?.permissions?.is_super_admin === true;
}

function list(v) {
  return Array.isArray(v) ? v : [];
}

export function label(envelope, kind, key) {
  const v = envelope?.vocabularies?.labels?.[kind]?.[key];
  return typeof v === "string" ? v : key ?? "";
}

/** May this caller change this definition at all? Writes need can_write; platform rows need Super Admin. */
function mayTouchDefinition(row, envelope) {
  if (!canWrite(envelope)) return false;
  if (row?.owner_scope === "platform") return isSuperAdmin(envelope);
  return true;
}

/** The lifecycle actions a definition row offers (each one a route in UDF_ROUTES), by its status. */
export function definitionActions(row, envelope) {
  if (!mayTouchDefinition(row, envelope)) return [];
  const editable = list(envelope?.vocabularies?.editable);
  switch (row?.status) {
    case "active":
      return [...(editable.length ? ["editDefinition"] : []), "deactivateDefinition", "deleteDefinition"];
    case "deactivated":
      return [...(editable.length ? ["editDefinition"] : []), "reactivateDefinition", "deleteDefinition"];
    case "deleted":
      return ["undeleteDefinition"];
    default:
      return [];
  }
}

export function tabActions(row, envelope) {
  if (!canWrite(envelope)) return [];
  switch (row?.status) {
    case "active":
      return ["editTab", "deactivateTab", "deleteTab"];
    case "deactivated":
      return ["editTab", "reactivateTab", "deleteTab"];
    case "deleted":
      return ["undeleteTab"];
    default:
      return [];
  }
}

export const ACTION_LABELS = Object.freeze({
  editDefinition: "Edit", deactivateDefinition: "Deactivate", reactivateDefinition: "Reactivate",
  deleteDefinition: "Delete", undeleteDefinition: "Undelete", editTab: "Rename", deactivateTab: "Deactivate",
  reactivateTab: "Reactivate", deleteTab: "Delete", undeleteTab: "Undelete",
});

// ── definition form ───────────────────────────────────────────────────────────────────────────────────────────

const INT_PARAMS = new Set(["length", "precision", "scale"]);
const NUM_PARAMS = new Set(["min", "max"]);

/** The type_params a data type requires / may carry — from the server (vocabularies.type_params). */
export function paramRules(envelope, dataType) {
  const r = envelope?.vocabularies?.type_params?.[dataType];
  return { required: list(r?.required), optional: list(r?.optional) };
}

export function slugKey(text) {
  return String(text || "").toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
}

/** Field-level errors for the create / edit form: {field: message}. Empty object = valid. */
export function validateDefinitionForm(form, envelope, { creating = true } = {}) {
  const errors = {};
  if (!String(form?.label || "").trim()) errors.label = "A name is required.";
  if (creating) {
    if (!/^[a-z][a-z0-9_]{0,59}$/.test(String(form?.field_key || ""))) {
      errors.field_key = "Key: lower-case letters, digits and underscores, starting with a letter.";
    }
    if (!list(envelope?.vocabularies?.applies_to).includes(form?.applies_to)) errors.applies_to = "Choose a record type.";
    if (!list(envelope?.vocabularies?.data_type).includes(form?.data_type)) errors.data_type = "Choose a field type.";
  }
  const { required, optional } = paramRules(envelope, form?.data_type);
  const params = form?.type_params || {};
  for (const p of required) {
    const v = params[p];
    if (v === undefined || v === null || String(v).trim() === "") errors[`type_params.${p}`] = "Required for this field type.";
    else if (INT_PARAMS.has(p) && !/^\d+$/.test(String(v).trim())) errors[`type_params.${p}`] = "Enter a whole number.";
  }
  for (const p of optional) {
    const v = params[p];
    if (v !== undefined && v !== null && String(v).trim() !== "" && NUM_PARAMS.has(p) && !Number.isFinite(Number(v))) {
      errors[`type_params.${p}`] = "Enter a number.";
    }
  }
  return errors;
}

/** Only the params this type accepts, typed (ints / numbers / trimmed strings); blanks dropped. */
export function buildTypeParams(envelope, dataType, raw) {
  const { required, optional } = paramRules(envelope, dataType);
  const out = {};
  for (const p of [...required, ...optional]) {
    const v = raw?.[p];
    if (v === undefined || v === null || String(v).trim() === "") continue;
    if (INT_PARAMS.has(p)) out[p] = parseInt(String(v).trim(), 10);
    else if (NUM_PARAMS.has(p)) out[p] = String(v).trim();
    else out[p] = String(v).trim();
  }
  return out;
}

function blankToNull(v) {
  return v === undefined || v === null || String(v).trim() === "" ? null : String(v).trim();
}

/** POST /udf/definitions body — org scope (the builder's namespace); never an org_id. */
export function definitionCreateBody(form, envelope) {
  return {
    owner_scope: "org",
    applies_to: form.applies_to,
    field_key: form.field_key,
    api_name: blankToNull(form.api_name) ?? form.field_key,
    label: String(form.label).trim(),
    data_type: form.data_type,
    type_params: buildTypeParams(envelope, form.data_type, form.type_params),
    help_text: blankToNull(form.help_text),
    description: blankToNull(form.description),
    is_required: Boolean(form.is_required),
    default_value: blankToNull(form.default_value),
    display_order: Number.isFinite(Number(form.display_order)) ? Number(form.display_order) : 0,
  };
}

const PATCH_KEYS = ["label", "help_text", "description", "is_required", "default_value", "display_order"];

/** The sparse PATCH body: only keys the server lists as editable AND that changed. */
export function definitionPatchBody(original, form, envelope) {
  const editable = new Set(list(envelope?.vocabularies?.editable));
  const out = {};
  for (const k of PATCH_KEYS) {
    if (!editable.has(k)) continue;
    let v = form?.[k];
    if (k === "is_required") v = Boolean(v);
    else if (k === "display_order") v = Number.isFinite(Number(v)) ? Number(v) : 0;
    else if (k !== "label") v = blankToNull(v);
    else v = String(v || "").trim();
    const before = original?.[k] ?? (k === "is_required" ? false : k === "display_order" ? 0 : null);
    if (v !== before) out[k] = v;
  }
  if (editable.has("type_params")) {
    const next = buildTypeParams(envelope, original?.data_type, form?.type_params);
    const prev = original?.type_params || {};
    const changed = Object.keys(next).some((k) => String(next[k]) !== String(prev[k] ?? ""));
    if (changed) out.type_params = next;
  }
  return out;
}

/** The text shown when a write is refused — the engine's own reason (e.g. a delete refused because values exist). */
export function refusalText(body, status) {
  const detail = body?.detail;
  const refs = detail && typeof detail === "object" ? detail.references : null;
  const base = typeof body?.error === "string" && body.error ? body.error
    : typeof detail === "string" ? detail : detail?.message || `Request failed (${status ?? "?"})`;
  if (refs && typeof refs === "object" && Object.keys(refs).length) {
    const parts = Object.entries(refs).map(([k, n]) => `${n} ${k.replace(/^udf_/, "").replace(/_/g, " ")}`);
    return `${base} (still referenced: ${parts.join(", ")})`;
  }
  return base;
}

// ── lists / filters ───────────────────────────────────────────────────────────────────────────────────────────

/** Client-side narrowing of the admin definitions list (target, status, type, free text). */
export function filterDefinitions(rows, { applies_to, status, data_type, q } = {}) {
  const needle = String(q || "").trim().toLowerCase();
  return list(rows).filter((r) => (!applies_to || r.applies_to === applies_to)
    && (!status || r.status === status)
    && (!data_type || r.data_type === data_type)
    && (!needle || `${r.label} ${r.field_key} ${r.api_name || ""}`.toLowerCase().includes(needle)));
}

export function placementText(row) {
  const p = list(row?.placements);
  if (!p.length) return "Not placed";
  return p.map((x) => `${x.tab_label}${x.section_title ? ` › ${x.section_title}` : ""}`).join("; ");
}

/** Move the element at `index` by `delta` (-1 up / +1 down); out of range → unchanged copy. */
export function moveInList(items, index, delta) {
  const arr = [...list(items)];
  const to = index + delta;
  if (index < 0 || index >= arr.length || to < 0 || to >= arr.length) return arr;
  const [x] = arr.splice(index, 1);
  arr.splice(to, 0, x);
  return arr;
}

/** Move id `activeId` to the position of `overId` (a drag end); unknown ids → unchanged copy. */
export function moveById(ids, activeId, overId) {
  const arr = [...list(ids)];
  const from = arr.indexOf(activeId);
  const to = arr.indexOf(overId);
  if (from < 0 || to < 0 || from === to) return arr;
  arr.splice(from, 1);
  arr.splice(to, 0, activeId);
  return arr;
}

/** The PATCH calls that give items the new order: one per item whose display_order changes. */
export function itemOrderPatches(items, orderedIds) {
  const byId = new Map(list(items).map((i) => [i.id, i]));
  const out = [];
  orderedIds.forEach((id, idx) => {
    const it = byId.get(id);
    if (it && it.display_order !== idx) out.push({ item: id, body: { display_order: idx } });
  });
  return out;
}

/** The tab PATCHes that give tabs the new order (display_order = position). */
export function tabOrderPatches(tabs, orderedIds) {
  const byId = new Map(list(tabs).map((t) => [t.id, t]));
  return orderedIds.flatMap((id, idx) => {
    const t = byId.get(id);
    return t && t.display_order !== idx ? [{ id, body: { display_order: idx } }] : [];
  });
}

/** Definitions that may still be placed on a tab: same target, active, not already on this layout. */
export function placeableDefinitions(defs, tab, layout) {
  const placed = new Set();
  for (const s of list(layout?.sections)) for (const i of list(s.items)) if (i.definition_id) placed.add(i.definition_id);
  return list(defs).filter((d) => d.applies_to === tab?.applies_to && d.status === "active" && !placed.has(d.id));
}

// ── permissions ───────────────────────────────────────────────────────────────────────────────────────────────

/** A grant body for PUT …/permissions: exactly one grantee (the engine's one-grantee CHECK). */
export function grantBody(granteeKind, granteeId, value, kind) {
  if (!granteeId || !["profile", "permission_set"].includes(granteeKind)) throw new Error("choose a profile or permission set");
  const who = granteeKind === "profile" ? { profile_id: granteeId } : { permission_set_id: granteeId };
  return kind === "tab" ? { ...who, is_visible: Boolean(value) } : { ...who, access: value };
}

/** Grant rows → [{kind, id, name, value}] with the grantee's name from the profile / permission-set lists. */
export function describeGrants(rows, profiles, sets, kind) {
  const pn = new Map(list(profiles).map((p) => [String(p.id), p.name]));
  const sn = new Map(list(sets).map((s) => [String(s.id), s.name]));
  return list(rows).map((r) => (r.profile_id
    ? { kind: "profile", id: r.profile_id, name: pn.get(r.profile_id) || r.profile_id }
    : { kind: "permission_set", id: r.permission_set_id, name: sn.get(r.permission_set_id) || r.permission_set_id }))
    .map((g, i) => ({ ...g, value: kind === "tab" ? Boolean(list(rows)[i].is_visible) : list(rows)[i].access }));
}

/** Preview rows: one per field — its access with no tab, and on each tab it is placed on (tab hidden ⇒ hidden). */
export function previewRows(preview, envelope) {
  const tabs = new Map(list(preview?.tabs).map((t) => [t.tab_id, t]));
  return list(preview?.fields).map((f) => ({
    definition_id: f.definition_id,
    label: f.label,
    access: f.access,
    accessLabel: label(envelope, "access", f.access),
    onTabs: list(f.on_tabs).map((t) => ({
      tab_id: t.tab_id, tab: tabs.get(t.tab_id)?.label || t.tab_id, access: t.access,
      accessLabel: label(envelope, "access", t.access),
    })),
  }));
}

// ── audit / records ───────────────────────────────────────────────────────────────────────────────────────────

export function auditRows(rows, envelope) {
  return list(rows).map((r) => ({
    id: r.id,
    when: r.changed_at,
    who: r.changed_by_name || r.changed_by_email || r.changed_by || "System",
    kind: label(envelope, "change_kind", r.change_kind),
    fields: list(r.changed_fields).join(", "),
  }));
}

/** DataGrid columnDefs for the records view: the route's own columns (base id + every available UDF column). */
export function recordColumns(envelope) {
  const cols = list(envelope?.columns).map((c) => ({
    field: c.definition_id, headerName: c.label || c.api_name || c.definition_id,
  }));
  return [{ field: "__name", headerName: "Record" }, ...cols];
}

/** Records rows → flat grid rows keyed by definition id (the route's own values, no recomputation). */
export function recordRows(envelope) {
  return list(envelope?.rows).map((r) => {
    const out = { id: r.id, __name: r.display_name || r.name || r.label || r.ticker || r.id };
    const udf = r.udf_values || {};
    for (const [k, v] of Object.entries(udf)) out[k] = Array.isArray(v) ? v.join(", ") : v;
    return out;
  });
}
