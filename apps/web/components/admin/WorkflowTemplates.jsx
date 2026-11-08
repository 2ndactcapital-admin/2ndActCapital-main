"use client";

/**
 * WorkflowTemplates — WFTEMPLATES1. Workflows → Templates (the catalog, a template page, "Use this template", this
 * firm's copies with "a newer version exists", save one of the firm's workflows as an org template) and Platform →
 * Workflow templates (draft, publish by a second Super Admin, retire, usage counts). Controls from
 * lib/workflowTemplates.mjs (fail closed); labels from the envelope's vocabularies and the server's own step labels.
 * The diagram component is passed in (`Diagram`) so this file carries no bpmn-js / CSS import. Light / cream only.
 */

import { useMemo, useState } from "react";
import {
  catalogControls, errorText, filterCatalog, initialValues, labelOf, newerNotice, platformControls,
  prerequisiteRows, stepRows, templateControls, usageText, useBody, vocab,
} from "@/lib/workflowTemplates.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";
const th = "px-2 py-1 text-left font-medium text-text-muted";
const td = "px-2 py-1 align-top";

export async function send(base, path, method = "GET", body) {
  const res = await fetch(`/api/${base}${path ? `/${path}` : ""}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "Request failed");
    err.status = res.status;
    err.detail = data.detail;
    throw err;
  }
  return data;
}

function Notice({ text, tone = "muted" }) {
  if (!text) return null;
  const color = tone === "error" ? "text-[#9B2335]" : tone === "ok" ? "text-[#2D6A4F]" : "text-text-muted";
  return <p className={`mt-2 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3 text-sm ${color}`}>{text}</p>;
}

// ── Catalog ─────────────────────────────────────────────────────────────────
export function CatalogView({ envelope, initialCategory = "", initialQuery = "" }) {
  const v = vocab(envelope);
  const [category, setCategory] = useState(initialCategory);
  const [q, setQ] = useState(initialQuery);
  const items = useMemo(() => filterCatalog(envelope?.templates, { category, q }), [envelope, category, q]);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Category" className={input} value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {(v.categories || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <input aria-label="Search templates" className={`${input} w-72`} placeholder="Search templates" value={q}
          onChange={(e) => setQ(e.target.value)} />
        <span className="text-sm text-text-muted">{items.length} template{items.length === 1 ? "" : "s"}</span>
      </div>
      {items.length === 0 ? (
        <Notice text="No template matches. Published platform templates and your firm's own templates appear here." />
      ) : (
        <ul className="mt-4 grid gap-4 md:grid-cols-2">
          {items.map((t) => (
            <li key={t.template_id} className={card}>
              <div className="flex items-baseline justify-between gap-3">
                <a href={`/admin/workflows/templates/${t.template_id}`} className="font-serif text-lg text-navy hover:underline">
                  {t.title}
                </a>
                <span className="text-xs text-text-muted">{labelOf(v.scopes, t.scope)}</span>
              </div>
              <p className="mt-1 text-xs uppercase tracking-wide text-text-muted">{labelOf(v.categories, t.category)} · version {t.version_no}</p>
              <p className="mt-2 line-clamp-3 text-sm text-[#334155]">{t.description}</p>
              <p className="mt-2 text-xs text-text-muted">
                {t.step_count} steps · {t.person_steps} done by a person · {t.approval_steps} needing approval
                {t.my_copies ? ` · ${t.my_copies} cop${t.my_copies === 1 ? "y" : "ies"} in your firm` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Save one of the firm's workflows as an org template ─────────────────────
export function SaveOrgTemplate({ envelope, workflows, onSaved }) {
  const v = vocab(envelope);
  const ctl = catalogControls(envelope);
  const [form, setForm] = useState({ workflow_definition_id: "", key: "", category: "", title: "", description: "" });
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!ctl.save) return null;
  const list = Array.isArray(workflows) ? workflows : [];
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const body = { workflow_definition_id: form.workflow_definition_id, key: form.key, category: form.category };
      if (form.title.trim()) body.title = form.title.trim();
      if (form.description.trim()) body.description = form.description.trim();
      const out = await send("workflow-templates", "org", "POST", body);
      setMsg({ tone: "ok", text: `Saved as your firm's template "${out.title}" (version ${out.version_no}). Only your firm sees it.` });
      onSaved?.(out);
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={`${card} mt-6`}>
      <h2 className="font-serif text-lg text-navy">Save a workflow as your firm&apos;s template</h2>
      <p className="mt-1 text-sm text-text-muted">The workflow&apos;s current version is checked by the same validator and saved for your firm only.</p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <select aria-label="Workflow" className={input} value={form.workflow_definition_id} onChange={set("workflow_definition_id")}>
          <option value="">Choose a workflow</option>
          {list.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <select aria-label="Template category" className={input} value={form.category} onChange={set("category")}>
          <option value="">Category</option>
          {(v.categories || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <input aria-label="Template key" className={input} placeholder="key (lower_snake_case)" value={form.key} onChange={set("key")} />
        <input aria-label="Template title" className={input} placeholder="Title (defaults to the workflow's name)" value={form.title} onChange={set("title")} />
        <textarea aria-label="Template description" className={`${input} md:col-span-2`} rows={3}
          placeholder="Plain-English description (defaults to the workflow's description)" value={form.description} onChange={set("description")} />
      </div>
      <button type="button" className={`${primary} mt-3`} disabled={busy || !form.workflow_definition_id || !form.key || !form.category}
        onClick={save}>Save as template</button>
      {msg && <Notice text={msg.text} tone={msg.tone} />}
    </section>
  );
}

// ── One template ────────────────────────────────────────────────────────────
function ParameterField({ p, value, onChange, profiles }) {
  const common = { "aria-label": p.label, className: `${input} w-full`, value: value ?? "", onChange: (e) => onChange(e.target.value) };
  if (p.kind === "choice") {
    return <select {...common}>{(p.choices || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>;
  }
  if (p.kind === "profile") {
    return (
      <select {...common}>
        <option value="">Unassigned (assign later in the editor)</option>
        {(profiles || []).map((pr) => <option key={pr.id} value={pr.id}>{pr.name}</option>)}
      </select>
    );
  }
  if (p.kind === "integer") return <input type="number" min={p.min} max={p.max} {...common} />;
  return <input type="text" maxLength={120} {...common} />;
}

export function UseTemplateForm({ envelope, detail, onCopied }) {
  const ctl = templateControls(envelope, detail);
  const params = detail?.version?.parameters || [];
  const [values, setValues] = useState(() => initialValues(params));
  const [name, setName] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!ctl.use) return null;
  async function use() {
    setBusy(true);
    setMsg(null);
    try {
      const out = await send("workflow-templates", `${detail.template.id}/use`, "POST",
        useBody(params, values, { versionId: detail.version.id, name }));
      setMsg({ tone: "ok", text: `Created the draft workflow "${out.name}". It has no trigger — nothing runs it until you add one on the Triggers screen.`,
        href: `/admin/workflows/${out.workflow_definition_id}/edit` });
      onCopied?.(out);
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={`${card} mt-6`}>
      <h2 className="font-serif text-lg text-navy">Use this template</h2>
      <p className="mt-1 text-sm text-text-muted">Creates a new draft workflow in your firm from version {detail.version.version_no}. Your copy is yours to edit; the template never changes it.</p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="text-sm text-[#334155] md:col-span-2">Workflow name
          <input aria-label="Workflow name" className={`${input} mt-1 w-full`} placeholder={detail.version.title} value={name}
            onChange={(e) => setName(e.target.value)} />
        </label>
        {params.map((p) => (
          <label key={p.name} className="text-sm text-[#334155]">{p.label}
            <div className="mt-1"><ParameterField p={p} value={values[p.name]} profiles={detail.profiles}
              onChange={(val) => setValues({ ...values, [p.name]: val })} /></div>
            {p.help && <span className="mt-1 block text-xs text-text-muted">{p.help}</span>}
          </label>
        ))}
      </div>
      <button type="button" className={`${primary} mt-3`} disabled={busy} onClick={use}>Create the draft workflow</button>
      {msg && <Notice text={msg.text} tone={msg.tone} />}
      {msg?.href && <a href={msg.href} className="mt-2 inline-block text-sm text-gold hover:underline">Open it in the editor</a>}
    </section>
  );
}

export function CopiesList({ copies }) {
  const list = Array.isArray(copies) ? copies : [];
  if (!list.length) return null;
  return (
    <section className={`${card} mt-6`}>
      <h2 className="font-serif text-lg text-navy">Your firm&apos;s copies</h2>
      <ul className="mt-2 space-y-3 text-sm">
        {list.map((c) => {
          const n = newerNotice(c);
          return (
            <li key={c.id}>
              {c.definition_exists ? (
                <a className="text-navy hover:underline" href={`/admin/workflows/${c.workflow_definition_id}/edit`}>{c.definition_name}</a>
              ) : <span className="text-text-muted">A workflow since deleted</span>}
              <span className="text-text-muted"> — from version {c.template_version_no}</span>
              {n && (
                <div className="mt-1 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3">
                  <p className="text-[#334155]">{n.text}</p>
                  {n.added.length > 0 && <p className="text-xs text-[#2D6A4F]">Added: {n.added.join(", ")}</p>}
                  {n.removed.length > 0 && <p className="text-xs text-[#9B2335]">Removed: {n.removed.join(", ")}</p>}
                  {n.changed.length > 0 && <p className="text-xs text-text-muted">Changed: {n.changed.join("; ")}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function TemplateView({ envelope, Diagram, onAction }) {
  const d = envelope || {};
  const v = vocab(envelope);
  const ver = d.version || {};
  const ctl = templateControls(envelope, d);
  const steps = stepRows(ver.steps);
  const pre = prerequisiteRows(d.prerequisites);
  const [msg, setMsg] = useState(null);
  async function retire() {
    const reason = typeof window !== "undefined" ? window.prompt("Why is this version being retired?") : "";
    if (!reason) return;
    try {
      await send("workflow-templates", `versions/${ver.id}/retire`, "POST", { reason });
      setMsg({ tone: "ok", text: "Retired. Existing copies keep working; it is no longer copied." });
      onAction?.();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    }
  }
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-text-muted">
        {labelOf(v.categories, ver.category)} · {labelOf(v.scopes, d.template?.scope)} · version {ver.version_no} · {labelOf(v.statuses, ver.status)}
      </p>
      {d.latest_published_version_no && d.latest_published_version_no > ver.version_no && (
        <Notice text={`A newer version (${d.latest_published_version_no}) is published.`} />
      )}
      <section className={`${card} mt-4`}>
        <h2 className="font-serif text-lg text-navy">What it does</h2>
        {String(ver.description || "").split(/\n\s*\n/).map((para, i) => <p key={i} className="mt-2 text-[#334155]">{para}</p>)}
      </section>
      <section className={`${card} mt-6`}>
        <h2 className="font-serif text-lg text-navy">Diagram</h2>
        {Diagram ? <Diagram xml={ver.drawable_xml} /> : <p className="mt-2 text-sm text-text-muted">The diagram loads in the browser.</p>}
      </section>
      <section className={`${card} mt-6`}>
        <h2 className="font-serif text-lg text-navy">Steps</h2>
        <table className="mt-2 w-full text-sm">
          <thead><tr><th className={th}>Step</th><th className={th}>Who</th><th className={th}>Verb</th><th className={th}>Governance</th></tr></thead>
          <tbody>
            {steps.map((s) => (
              <tr key={s.key} className="border-t border-[#E2E8F0]">
                <td className={td}>{s.name}</td><td className={td}>{s.kind}</td>
                <td className={`${td} font-mono text-xs`}>{s.verb}</td>
                <td className={`${td} ${s.needsApproval ? "font-medium text-[#9B2335]" : "text-text-muted"}`}>{s.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {(ver.parameters || []).length > 0 && (
        <section className={`${card} mt-6`}>
          <h2 className="font-serif text-lg text-navy">Parameters</h2>
          <ul className="mt-2 space-y-1 text-sm text-[#334155]">
            {ver.parameters.map((p) => (
              <li key={p.name}>{p.label}{p.default !== null && p.default !== undefined ? ` — default ${p.default}` : ""}
                {p.help ? <span className="text-text-muted"> · {p.help}</span> : null}</li>
            ))}
          </ul>
        </section>
      )}
      <section className={`${card} mt-6`}>
        <h2 className="font-serif text-lg text-navy">What your firm needs</h2>
        <ul className="mt-2 space-y-1 text-sm">
          {pre.map((p) => (
            <li key={p.key} className={p.state === "missing" ? "text-[#9B2335]" : "text-[#334155]"}>
              <span className="font-mono text-xs">{p.key}</span> — {p.why}
              {p.state === "met" && <span className="text-[#2D6A4F]"> · in place</span>}
              {p.state === "missing" && <span> · missing: {p.missing}</span>}
            </li>
          ))}
        </ul>
      </section>
      <UseTemplateForm envelope={envelope} detail={d} onCopied={onAction} />
      {ctl.retire && <button type="button" className={`${secondary} mt-4`} onClick={retire}>Retire this version</button>}
      {msg && <Notice text={msg.text} tone={msg.tone} />}
      <CopiesList copies={d.my_copies} />
    </div>
  );
}

// ── Platform ────────────────────────────────────────────────────────────────
export function PlatformDraftForm({ envelope, onDrafted }) {
  const v = vocab(envelope);
  const ctl = platformControls(envelope, null);
  const [form, setForm] = useState({ key: "", title: "", category: "", description: "", bpmn_xml: "", parameters: "[]" });
  const [msg, setMsg] = useState(null);
  if (!ctl.draft) return null;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  async function draft() {
    setMsg(null);
    let parameters;
    try {
      parameters = JSON.parse(form.parameters || "[]");
    } catch {
      setMsg({ tone: "error", text: "Parameters must be a JSON list." });
      return;
    }
    try {
      const out = await send("platform-workflow-templates", "", "POST", { ...form, parameters });
      setMsg({ tone: "ok", text: `Drafted version ${out.version_no}. A different Super Admin publishes it.` });
      onDrafted?.(out);
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    }
  }
  return (
    <section className={`${card} mt-6`}>
      <h2 className="font-serif text-lg text-navy">Draft a platform template version</h2>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <input aria-label="Key" className={input} placeholder="key" value={form.key} onChange={set("key")} />
        <input aria-label="Title" className={input} placeholder="Title" value={form.title} onChange={set("title")} />
        <select aria-label="Category" className={input} value={form.category} onChange={set("category")}>
          <option value="">Category</option>
          {(v.categories || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <textarea aria-label="Description" className={`${input} md:col-span-2`} rows={3} placeholder="Plain-English description" value={form.description} onChange={set("description")} />
        <textarea aria-label="BPMN XML" className={`${input} font-mono text-xs md:col-span-2`} rows={6} placeholder="BPMN XML" value={form.bpmn_xml} onChange={set("bpmn_xml")} />
        <textarea aria-label="Parameters (JSON)" className={`${input} font-mono text-xs md:col-span-2`} rows={3} value={form.parameters} onChange={set("parameters")} />
      </div>
      <button type="button" className={`${primary} mt-3`} onClick={draft}>Draft</button>
      {msg && <Notice text={msg.text} tone={msg.tone} />}
    </section>
  );
}

export function PlatformView({ envelope, onAction }) {
  const v = vocab(envelope);
  const [msg, setMsg] = useState(null);
  const templates = Array.isArray(envelope?.templates) ? envelope.templates : [];
  async function act(versionId, path, body, done) {
    setMsg(null);
    try {
      await send("platform-workflow-templates", `versions/${versionId}/${path}`, "POST", body);
      setMsg({ tone: "ok", text: done });
      onAction?.();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    }
  }
  const ask = (q) => (typeof window !== "undefined" ? window.prompt(q) : "");
  return (
    <div>
      {envelope?.platform_org_allowed === false && (
        <Notice text="Platform templates are drafted in the Hollisworks platform org; this org cannot own them." />
      )}
      <PlatformDraftForm envelope={envelope} onDrafted={onAction} />
      {templates.length === 0 ? <Notice text="No platform templates yet. Load the shipped catalog with the operator script, then publish each draft." /> : (
        <table className="mt-6 w-full text-sm">
          <thead><tr><th className={th}>Template</th><th className={th}>Version</th><th className={th}>Status</th><th className={th}>Usage</th><th className={th} /></tr></thead>
          <tbody>
            {templates.flatMap((t) => (t.versions || []).map((ver) => {
              const ctl = platformControls(envelope, ver);
              return (
                <tr key={ver.id} className="border-t border-[#E2E8F0]">
                  <td className={td}>{ver.title} <span className="font-mono text-xs text-text-muted">{t.key}</span></td>
                  <td className={td}>{ver.version_no}</td>
                  <td className={td}>{labelOf(v.statuses, ver.status)}</td>
                  <td className={`${td} text-text-muted`}>{usageText(ver)}</td>
                  <td className={`${td} space-x-2 text-right`}>
                    {ctl.decide && <button type="button" className={primary}
                      onClick={() => act(ver.id, "decide", { outcome: "approve" }, "Published.")}>Publish</button>}
                    {ctl.decide && <button type="button" className={secondary}
                      onClick={() => { const note = ask("Why is it rejected?"); if (note) act(ver.id, "decide", { outcome: "reject", note }, "Rejected."); }}>Reject</button>}
                    {ctl.retire && <button type="button" className={secondary}
                      onClick={() => { const reason = ask("Why is it retired?"); if (reason) act(ver.id, "retire", { reason }, "Retired. Existing copies keep working."); }}>Retire</button>}
                  </td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      )}
      {msg && <Notice text={msg.text} tone={msg.tone} />}
    </div>
  );
}
