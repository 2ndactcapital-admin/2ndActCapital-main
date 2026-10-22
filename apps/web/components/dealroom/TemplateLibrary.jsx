"use client";

/**
 * TemplateLibrary — the firm's agreement template library (DEALROOM2).
 *
 * Pure render of GET /deal-room/library (org admins — manage_org_settings —
 * and Super Admin). Platform templates are listed beside the firm's own; a
 * platform template can be COPIED into the firm library, and only Super Admin
 * edits one. A draft is edited in place; publishing makes it active (the
 * firm's previous active version retires but is kept forever); a version that
 * left draft, or that a deal uses, changes only through a new version. Every
 * control comes from the server: `permissions.can_write === true` AND the
 * row's own can_edit / can_publish / can_add_version / can_copy flags.
 */

import { useState } from "react";
import { libraryActions } from "@/lib/dealRoom.mjs";

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api/deal-room/${path}`, {
    method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function Editor({ row, onSave }) {
  const [title, setTitle] = useState(row.title);
  const [body, setBody] = useState(row.body);
  const [fields, setFields] = useState(JSON.stringify(row.fields || [], null, 2));
  return (
    <div className="mt-3 space-y-2 text-sm" data-testid="template-editor">
      <input aria-label="Title" className="block w-full rounded border border-border px-2 py-1" value={title}
        onChange={(e) => setTitle(e.target.value)} />
      <textarea aria-label="Text" className="block h-64 w-full rounded border border-border px-2 py-1 font-mono text-xs"
        value={body} onChange={(e) => setBody(e.target.value)} />
      <textarea aria-label="Fields (JSON)" className="block h-32 w-full rounded border border-border px-2 py-1 font-mono text-xs"
        value={fields} onChange={(e) => setFields(e.target.value)} />
      <button type="button" className="rounded bg-navy px-3 py-1.5 text-white"
        onClick={() => onSave({ title, body, fields: JSON.parse(fields) })}>Save draft</button>
    </div>
  );
}

export default function TemplateLibrary({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [open, setOpen] = useState(null);
  const [msg, setMsg] = useState(null);
  if (!env) return <p className="text-sm text-text-muted">The template library is not available.</p>;
  const run = async (fn) => {
    try {
      setMsg(null);
      await fn();
      setEnv(await call("library"));
    } catch (e) {
      setMsg(e.message);
    }
  };
  const groups = [
    { title: "Firm library", rows: (env.rows || []).filter((r) => r.scope === "org") },
    { title: "Platform templates", rows: (env.rows || []).filter((r) => r.scope === "platform") },
  ];
  return (
    <div className="space-y-6">
      {groups.map((g) => (
        <section key={g.title} className="rounded-[6px] border border-[#ece8dd] bg-white p-5">
          <h2 className="text-base font-semibold text-navy">{g.title}</h2>
          {g.rows.length === 0 && <p className="mt-2 text-sm text-text-muted">None yet.</p>}
          <ul className="mt-2 divide-y divide-[#ece8dd]">
            {g.rows.map((r) => {
              const actions = libraryActions(env, r);
              return (
                <li key={r.id} className="py-3" data-template={r.key}>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="font-medium text-navy">{r.title}{" "}
                      <span className="text-xs text-text-muted">{r.key} · v{r.version} · {r.status}
                        {r.in_use ? ` · used by ${r.in_use} deal agreement${r.in_use === 1 ? "" : "s"}` : ""}</span>
                    </span>
                    <span className="flex gap-3 text-xs">
                      {actions.includes("copy") && (
                        <button type="button" className="text-navy hover:underline"
                          onClick={() => run(() => call("library", { method: "POST", body: { source_template_id: r.id } }))}>
                          Copy to firm library</button>
                      )}
                      {actions.includes("edit") && (
                        <button type="button" className="text-navy hover:underline"
                          onClick={() => setOpen(open === r.id ? null : r.id)}>Edit</button>
                      )}
                      {actions.includes("publish") && (
                        <button type="button" className="text-navy hover:underline"
                          onClick={() => run(() => call(`library/${r.id}/publish`, { method: "POST" }))}>Publish</button>
                      )}
                      {actions.includes("add_version") && (
                        <button type="button" className="text-navy hover:underline"
                          onClick={() => run(() => call(`library/${r.id}/versions`, { method: "POST" }))}>New version</button>
                      )}
                    </span>
                  </div>
                  {open === r.id && actions.includes("edit") && (
                    <Editor row={r} onSave={(changes) => run(async () => {
                      await call(`library/${r.id}`, { method: "PATCH", body: changes });
                      setOpen(null);
                    })} />
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {msg && <p className="text-sm text-[#9B2335]" role="alert">{msg}</p>}
    </div>
  );
}
