"use client";

/**
 * QAPanel — a deal's questions and answers (DEALROOM3).
 *
 * Renders GET /deal-room/deals/{id}/questions. Investors see their own
 * questions and every answered question their access reaches — the server
 * never sends another investor's identity (`mine` only). Ask renders only when
 * `permissions.can_ask === true`; a manager's answer / withdraw / merge only
 * when `permissions.can_write === true` (lib/dealRoom.qaControls) — no fallback.
 */

import { useState } from "react";
import { qaControls } from "@/lib/dealRoom.mjs";

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api/deal-room/${path}`, {
    method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function ManagerControls({ dealId, q, others, refresh, setError, actions }) {
  const [answer, setAnswer] = useState(q.answer || "");
  const [into, setInto] = useState("");
  const run = async (fn) => {
    try { setError(null); await fn(); await refresh(); } catch (e) { setError(e.message); }
  };
  return (
    <div className="mt-2 space-y-2 text-xs" data-testid="qa-manager">
      {actions.includes("answer") && (
        <div>
          <textarea aria-label="Answer" className="block w-full rounded border border-border px-2 py-1 text-sm" rows={2}
            value={answer} onChange={(e) => setAnswer(e.target.value)} />
          <button type="button" className="mt-1 rounded bg-navy px-3 py-1 text-white" disabled={answer.trim().length < 2}
            onClick={() => run(() => call(`deals/${dealId}/questions/${q.id}/answer`, { method: "POST", body: { body: answer } }))}>
            {q.answer ? "Update answer" : "Publish answer"}
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {actions.includes("withdraw") && (
          <button type="button" className="text-[#9B2335] hover:underline" onClick={() => {
            const reason = window.prompt("Reason for withdrawing this question");
            if (reason) run(() => call(`deals/${dealId}/questions/${q.id}/withdraw`, { method: "POST", body: { reason } }));
          }}>Withdraw</button>
        )}
        {actions.includes("merge") && others.length > 0 && (
          <>
            <select aria-label="Merge into" className="rounded border border-border bg-white px-1" value={into}
              onChange={(e) => setInto(e.target.value)}>
              <option value="">Merge into…</option>
              {others.map((o) => <option key={o.id} value={o.id}>{o.body.slice(0, 60)}</option>)}
            </select>
            <button type="button" disabled={!into} className="text-navy hover:underline disabled:opacity-40" onClick={() => {
              const reason = window.prompt("Reason for merging (e.g. duplicate)") || "";
              if (reason) run(() => call(`deals/${dealId}/questions/${q.id}/merge`, { method: "POST", body: { into_id: into, reason } }));
            }}>Merge</button>
          </>
        )}
      </div>
    </div>
  );
}

export default function QAPanel({ dealId, initial, folders = [] }) {
  const [qa, setQa] = useState(initial);
  const [body, setBody] = useState("");
  const [folderId, setFolderId] = useState("");
  const [error, setError] = useState(null);
  const ctl = qaControls(qa);
  const refresh = async () => setQa(await call(`deals/${dealId}/questions`));
  const rows = qa?.rows || [];
  const live = rows.filter((r) => r.status === "open" || r.status === "answered");
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-5" data-testid="qa-panel">
      <h3 className="text-base font-semibold text-navy">Questions and answers</h3>
      {rows.length === 0 && <p className="mt-2 text-sm text-text-muted">No questions yet.</p>}
      <ul className="mt-3 space-y-3 text-sm">
        {rows.map((q) => (
          <li key={q.id} className="rounded border border-border p-3" data-question={q.id}>
            <p className="text-text-secondary">{q.body}</p>
            <p className="mt-1 text-xs text-text-muted">
              {q.status_label || q.status}{q.folder_label ? ` · ${q.folder_label}` : " · General"}
              {q.mine ? " · your question" : ""}
              {q.asker_name ? ` · asked by ${q.asker_name}${q.asker_entity ? ` (${q.asker_entity})` : ""}` : ""}
              {q.status_reason ? ` · ${q.status_reason}` : ""}
            </p>
            {q.answer && <p className="mt-2 border-l-2 border-[#C5A880] pl-3 text-text-secondary">{q.answer}</p>}
            {ctl.manage.length > 0 && (
              <ManagerControls dealId={dealId} q={q} others={live.filter((o) => o.id !== q.id)} refresh={refresh}
                setError={setError} actions={q.status === "withdrawn" || q.status === "merged" ? [] : ctl.manage} />
            )}
          </li>
        ))}
      </ul>
      {ctl.ask && (
        <div className="mt-4 space-y-2 text-sm" data-testid="qa-ask">
          <textarea aria-label="Your question" className="block w-full rounded border border-border px-2 py-1.5" rows={3}
            value={body} onChange={(e) => setBody(e.target.value)} />
          <select aria-label="About" className="rounded border border-border bg-white px-2 py-1 text-xs" value={folderId}
            onChange={(e) => setFolderId(e.target.value)}>
            <option value="">General question</option>
            {folders.filter((f) => f.open).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
          <p className="text-xs text-text-muted">Other investors see the answer, never who asked.</p>
          <button type="button" disabled={body.trim().length < 3}
            className="rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            onClick={async () => {
              try {
                setError(null);
                await call(`deals/${dealId}/questions`, { method: "POST", body: { body, folder_id: folderId || null } });
                setBody("");
                await refresh();
              } catch (e) { setError(e.message); }
            }}>Ask</button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
  );
}
