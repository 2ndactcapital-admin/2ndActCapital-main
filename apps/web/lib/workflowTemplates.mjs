// WFTEMPLATES1 — the pure logic behind Workflows → Templates (browse, preview, use, save as an org template) and
// Platform → Workflow templates (draft, publish by a second Super Admin, retire, usage counts). Every control is
// decided HERE from the server's envelope and FAILS CLOSED: a control shows only when the server's own permission
// flag is exactly `true` AND the server's vocabulary lists the action — a missing, partial or malformed envelope
// shows none (never a client default). Labels come from the envelope's vocabularies and the server's own step
// labels; nothing here invents a tier or a category name.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function perms(envelope) {
  return obj(obj(envelope).permissions);
}

export function vocab(envelope) {
  return obj(obj(envelope).vocabularies);
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

const editable = (env) => listOf(vocab(env).editable);
const platformEditable = (env) => listOf(vocab(env).platform_editable);

/** Catalog / template-page controls. Use needs can_write + "use" AND a published version the server says may be
 * copied; save-as-org-template needs can_write + "save"; retiring an ORG version needs can_write + "retire" and a
 * published version this org owns. */
export function templateControls(envelope, detail) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const d = obj(detail);
  const v = obj(d.version);
  const own = isTrue(obj(d.template).is_own) && obj(d.template).scope === "org";
  return {
    use: w && editable(envelope).includes("use") && isTrue(d.can_use_version),
    save: w && editable(envelope).includes("save"),
    retire: w && editable(envelope).includes("retire") && own && v.status === "published",
  };
}

/** The catalog-level controls (the save-as-template panel). */
export function catalogControls(envelope) {
  const p = perms(envelope);
  return { save: isTrue(p.can_write) && editable(envelope).includes("save") };
}

/** Platform screen: every control needs is_super_admin === true AND the platform action listed. Deciding also needs
 * the server's own per-version can_decide (never the drafter — maker-checker); retiring needs can_retire. */
export function platformControls(envelope, version) {
  const sa = isTrue(perms(envelope).is_super_admin);
  const pe = platformEditable(envelope);
  const v = obj(version);
  return {
    draft: sa && pe.includes("draft"),
    decide: sa && pe.includes("decide") && isTrue(v.can_decide),
    retire: sa && pe.includes("retire") && isTrue(v.can_retire),
  };
}

/** Filter the catalog by category and a search string (title, description, key) — client-side over the list the
 * server returned; the server applies the same filters when asked. */
export function filterCatalog(items, { category = "", q = "" } = {}) {
  const needle = String(q || "").trim().toLowerCase();
  return listOf(items).filter((t) => {
    const it = obj(t);
    if (category && it.category !== category) return false;
    if (!needle) return true;
    return [it.title, it.description, it.key].some((s) => String(s || "").toLowerCase().includes(needle));
  });
}

/** Steps as the page shows them: the server's own label; "needs approval" only when the server says so. */
export function stepRows(steps) {
  return listOf(steps).map((s) => {
    const x = obj(s);
    return {
      key: x.step_key,
      name: x.display_name || x.step_key,
      kind: x.step_type === "service" ? "Automated" : x.step_type === "user" ? "Person" : (x.step_type || ""),
      verb: x.action_registry_key || "",
      label: x.label || "",
      needsApproval: isTrue(x.needs_approval),
      tier: Number.isInteger(x.effective_tier) ? x.effective_tier : null,
    };
  });
}

/** The prerequisites the server found missing (its own words), and the ones it could not decide. */
export function prerequisiteRows(prereqs) {
  return listOf(prereqs).map((p) => {
    const x = obj(p);
    return { key: x.key, why: x.why || "", state: x.met === true ? "met" : x.met === false ? "missing" : "unknown",
      missing: x.missing || "" };
  });
}

/** The form's starting values: each parameter's declared default (a profile starts empty). */
export function initialValues(parameters) {
  const out = {};
  for (const p of listOf(parameters)) {
    const x = obj(p);
    if (!x.name) continue;
    out[x.name] = x.kind === "profile" || x.default === null || x.default === undefined ? "" : String(x.default);
  }
  return out;
}

/** The body POST /use sends: only declared parameters; an integer as a number; empty strings dropped. */
export function useBody(parameters, values, { versionId, name } = {}) {
  const params = {};
  for (const p of listOf(parameters)) {
    const x = obj(p);
    const raw = obj(values)[x.name];
    if (raw === undefined || raw === null || String(raw).trim() === "") continue;
    params[x.name] = x.kind === "integer" && /^-?\d+$/.test(String(raw).trim()) ? Number(String(raw).trim()) : String(raw).trim();
  }
  const body = { parameters: params };
  if (versionId) body.version_id = versionId;
  if (name && String(name).trim()) body.name = String(name).trim();
  return body;
}

/** "A newer template version exists" for one copy — null when it is current. Never offers to overwrite. */
export function newerNotice(copy) {
  const c = obj(copy);
  const n = obj(c.newer_version);
  if (!c.newer_version || !Number.isInteger(n.version_no)) return null;
  const d = obj(n.diff);
  return {
    text: `A newer version of this template exists (version ${n.version_no}; your copy came from version `
      + `${c.template_version_no}). Your workflow is unchanged — start a new copy to use it.`,
    added: listOf(d.added).map((x) => obj(x).display_name || obj(x).step_key),
    removed: listOf(d.removed).map((x) => obj(x).display_name || obj(x).step_key),
    changed: listOf(d.changed).map((x) => `${obj(x).step_key}: ${obj(x).field} ${JSON.stringify(obj(x).before)} → `
      + `${JSON.stringify(obj(x).after)}`),
  };
}

/** Usage on the platform screen: counts only (copies, organisations) — never a name. */
export function usageText(version) {
  const v = obj(version);
  const c = Number.isInteger(v.copies) ? v.copies : 0;
  const o = Number.isInteger(v.orgs) ? v.orgs : 0;
  return `${c} ${c === 1 ? "copy" : "copies"} in ${o} ${o === 1 ? "organisation" : "organisations"}`;
}

/** A refusal's message: the server's own detail (a 422 carries {message, errors}). */
export function errorText(err) {
  const e = obj(err);
  const d = e.detail;
  if (d && typeof d === "object") {
    const errs = listOf(d.errors);
    return [d.message, ...errs].filter(Boolean).join(" — ");
  }
  return e.message || "Request failed";
}
