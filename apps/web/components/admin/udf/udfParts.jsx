"use client";

// UDFUI1 — shared pieces of the custom-fields builder: the ONE call helper (through the Next forwarder, refusals
// carrying the engine's own reason), and small accessible form controls (label tied to the input, error tied by
// aria-describedby + aria-invalid).
import { builderRequest, refusalText } from "@/lib/udfBuilder.mjs";

export async function udfCall(action, params, { query, body } = {}) {
  const { method, url } = builderRequest(action, params, query);
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(refusalText(data, res.status));
  return data;
}

export const inputCls = "mt-1 block w-full rounded border border-border bg-white px-2 py-1 text-sm text-text-primary";
export const btnCls = "rounded border border-border bg-white px-2 py-1 text-sm text-navy hover:bg-bg-sidebar disabled:opacity-50";
export const primaryCls = "rounded bg-navy px-3 py-1 text-sm text-white hover:opacity-90 disabled:opacity-50";

export function Field({ id, label, error, hint, children }) {
  const errId = error ? `${id}-error` : undefined;
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="mb-3">
      <label htmlFor={id} className="block text-sm font-medium text-text-secondary">{label}</label>
      {children({ id, "aria-invalid": error ? "true" : undefined,
        "aria-describedby": [errId, hintId].filter(Boolean).join(" ") || undefined })}
      {hint ? <p id={hintId} className="mt-1 text-xs text-text-muted">{hint}</p> : null}
      {error ? <p id={errId} role="alert" className="mt-1 text-xs text-[#9B2335]">{error}</p> : null}
    </div>
  );
}

export function Select({ id, label, value, onChange, options, error, hint, placeholder, disabled }) {
  return (
    <Field id={id} label={label} error={error} hint={hint}>
      {(a) => (
        <select {...a} className={inputCls} value={value ?? ""} disabled={disabled}
          onChange={(e) => onChange(e.target.value)}>
          {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )}
    </Field>
  );
}

export function Text({ id, label, value, onChange, error, hint, type = "text", disabled }) {
  return (
    <Field id={id} label={label} error={error} hint={hint}>
      {(a) => (
        <input {...a} type={type} className={inputCls} value={value ?? ""} disabled={disabled}
          onChange={(e) => onChange(e.target.value)} />
      )}
    </Field>
  );
}

export function Notice({ children, tone = "muted" }) {
  const cls = tone === "error" ? "text-[#9B2335]" : "text-text-muted";
  return <p role={tone === "error" ? "alert" : undefined} className={`text-sm ${cls}`}>{children}</p>;
}

export function Card({ title, children, actions }) {
  return (
    <section className="mt-4 rounded-md border border-[#ece8dd] bg-white p-4">
      {(title || actions) ? (
        <div className="mb-3 flex items-center justify-between">
          {title ? <h2 className="text-lg font-semibold text-navy">{title}</h2> : <span />}
          {actions || null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
