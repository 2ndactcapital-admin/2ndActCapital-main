"use client";

/**
 * Describe a fee arrangement, read the diff, recognise the dollar figure.
 *
 * THE WRITE CONTROLS RENDER ONLY INSIDE A can_write CHECK, WITH NO FALLBACK
 * ─────────────────────────────────────────────────────────────────────────
 * `permissions` and `vocabularies` come from the server's own envelope. There
 * is deliberately no `|| DEFAULTS` anywhere below: a lost envelope must fail
 * CLOSED, and a truthy fallback is exactly the pattern that silently restores
 * full write access when the envelope goes missing for an unrelated reason.
 * `editable` is an empty array for a view-only caller, so every field renders
 * read-only without this component knowing anything about permissions itself.
 *
 * UNRESOLVED IS NOT A BLANK
 * ─────────────────────────────────────────────────────────────────────────
 * A blank cell reads as "unchanged". An unresolved field means nobody knows
 * yet, and it blocks both the save and the worked example — so it is drawn in
 * the error colour with its reason spelled out, never as an empty input.
 */

import { useCallback, useMemo, useState } from "react";

import {
  canWrite as gateCanWrite,
  choicesFor,
  mayEditField,
  saveEnabled,
  showSaveControl,
} from "@/lib/feeChatGates";

const NAVY = "var(--2a-navy, #1B2B4B)";
const GOLD = "var(--2a-gold, #C5A880)";
const ERROR = "#9B2335";
const SUCCESS = "#2D6A4F";
const MUTED = "var(--2a-text-muted, #64748B)";
const HAIRLINE = "#ece8dd";

const CARD =
  "rounded-[6px] border border-[#ece8dd] bg-white p-5";

const STATUS_STYLE = {
  changed: { label: "changed", color: NAVY, weight: 600 },
  new: { label: "new", color: SUCCESS, weight: 600 },
  unchanged: { label: "unchanged", color: MUTED, weight: 400 },
  unresolved: { label: "unresolved", color: ERROR, weight: 600 },
  not_specified: { label: "not specified", color: MUTED, weight: 400 },
  added: { label: "added", color: SUCCESS, weight: 600 },
  removed: { label: "removed", color: ERROR, weight: 600 },
};

function fmtMoney(value, currency) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: currency || "USD",
    minimumFractionDigits: 2,
  });
}

function fmtValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join(" → ");
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

export default function FeeChatWorkbench() {
  const [description, setDescription] = useState("");
  const [result, setResult] = useState(null);
  const [example, setExample] = useState(null);
  const [exampleError, setExampleError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(null);

  // field -> { original, corrected }. The advisor's edits, held until the
  // worked example or the save sends them; each one is logged as a correction.
  const [edits, setEdits] = useState({});

  // Straight from the server's envelope. Every gate below is lib/feeChatGates,
  // which fails CLOSED on a missing or malformed envelope — no local
  // re-implementation and no truthy fallback.
  const permissions = result?.permissions ?? null;
  const vocabularies = result?.vocabularies ?? null;
  const canWrite = gateCanWrite(permissions);

  // The spec as it stands now: the model's, with the advisor's edits applied
  // and every edited field marked so the server exempts it from the grounding
  // check (a human stating a value is the authority for it).
  const currentSpec = useMemo(() => {
    if (!result?.spec) return null;
    const schedule = { ...result.spec.schedule };
    const advisorSet = [];
    for (const [field, pair] of Object.entries(edits)) {
      if (pair.corrected === "" || pair.corrected === undefined) continue;
      schedule[field] = pair.corrected;
      advisorSet.push(field);
    }
    return { ...result.spec, schedule, advisor_set: advisorSet };
  }, [result, edits]);

  const post = useCallback(async (path, body) => {
    const res = await fetch(`/api/fee-chat/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw data?.error ?? { message: "Request failed" };
    return data;
  }, []);

  const onPropose = useCallback(async () => {
    setBusy("propose");
    setError(null);
    setExample(null);
    setExampleError(null);
    setSaved(null);
    setEdits({});
    try {
      setResult(await post("propose", { description }));
    } catch (err) {
      setError(err);
      setResult(null);
    } finally {
      setBusy(null);
    }
  }, [description, post]);

  const onWorkedExample = useCallback(async () => {
    if (!currentSpec) return;
    setBusy("example");
    setExample(null);
    setExampleError(null);
    try {
      const data = await post("worked-example", {
        spec: currentSpec,
        description,
      });
      setExample(data.worked_example);
    } catch (err) {
      // A refusal is shown as a refusal. Never a zero, never a stale figure.
      setExampleError(err);
    } finally {
      setBusy(null);
    }
  }, [currentSpec, description, post]);

  const onSave = useCallback(async () => {
    if (!currentSpec) return;
    setBusy("save");
    setError(null);
    try {
      // Corrections are logged BEFORE the save, so an edit is recorded even if
      // the save is then refused — a refused save is still a real signal about
      // where the model was wrong.
      const changed = Object.fromEntries(
        Object.entries(edits).filter(([, p]) => p.original !== p.corrected),
      );
      if (Object.keys(changed).length && result?.conversation_id) {
        await post("corrections", {
          conversation_id: result.conversation_id,
          edits: changed,
        });
      }
      const data = await post("save", {
        spec: currentSpec,
        description,
        conversation_id: result?.conversation_id ?? null,
      });
      setSaved(data.schedule);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }, [currentSpec, description, edits, post, result]);

  const setField = useCallback((field, original, value) => {
    setEdits((prev) => ({ ...prev, [field]: { original, corrected: value } }));
  }, []);

  const validationErrors = result?.validation_errors ?? [];
  const errorsByField = useMemo(() => {
    const map = {};
    for (const e of validationErrors) {
      if (!e.field) continue;
      (map[e.field] ??= []).push(e);
    }
    return map;
  }, [validationErrors]);

  return (
    <div className="space-y-5">
      {/* ── the description ─────────────────────────────────────────── */}
      <section className={CARD}>
        <label
          htmlFor="fee-description"
          className="block text-[11px] font-bold uppercase tracking-[0.22em]"
          style={{ color: GOLD }}
        >
          Describe the arrangement
        </label>
        <textarea
          id="fee-description"
          rows={4}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="100 basis points on the first million, 75 above that, graduated. Quarterly in arrears, on the period-end market value."
          className="mt-3 w-full rounded-[6px] border border-[#E2E8F0] p-3 text-[15px] text-[#0F172A] outline-none focus:border-[#C5A880]"
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={onPropose}
            disabled={busy !== null || !description.trim()}
            className="rounded-[6px] px-4 py-2 text-[14px] font-medium text-white disabled:opacity-40"
            style={{ backgroundColor: NAVY }}
          >
            {busy === "propose" ? "Reading…" : "Draft the schedule"}
          </button>
          <p className="text-[13px]" style={{ color: MUTED }}>
            The assistant proposes the rule. Every figure below is computed by
            the billing engine from real balances.
          </p>
        </div>
      </section>

      {error ? (
        <section className={CARD} style={{ borderColor: ERROR }}>
          <p className="text-[14px] font-semibold" style={{ color: ERROR }}>
            {error.message ?? "Something went wrong."}
          </p>
          {Array.isArray(error.errors) && error.errors.length ? (
            <ul className="mt-2 space-y-1 text-[13px]" style={{ color: ERROR }}>
              {error.errors.map((e, i) => (
                <li key={i}>
                  {e.field ? <strong>{e.field}: </strong> : null}
                  {e.message}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      {saved ? (
        <section className={CARD} style={{ borderColor: SUCCESS }}>
          <p className="text-[14px] font-semibold" style={{ color: SUCCESS }}>
            Saved as draft {saved.code} v{saved.version}. It still needs
            approval before anything is billed on it.
          </p>
        </section>
      ) : null}

      {result ? (
        <>
          {/* ── the diff ────────────────────────────────────────────── */}
          <section className={CARD}>
            <header className="mb-4">
              <h2
                className="text-[19px] font-semibold"
                style={{ color: NAVY, fontFamily: "Spectral, Georgia, serif" }}
              >
                What changes
              </h2>
              <p className="mt-1 text-[13px]" style={{ color: MUTED }}>
                Compared against {result.diff.baseline_label}.
              </p>
            </header>

            <table className="w-full text-[14px]">
              <thead>
                <tr
                  className="text-left text-[11px] uppercase tracking-[0.14em]"
                  style={{ color: MUTED }}
                >
                  <th className="pb-2 font-semibold">Field</th>
                  <th className="pb-2 font-semibold">Current</th>
                  <th className="pb-2 font-semibold">Proposed</th>
                  <th className="pb-2 font-semibold">State</th>
                </tr>
              </thead>
              <tbody>
                {result.diff.fields.map((row) => {
                  const style = STATUS_STYLE[row.status] ?? STATUS_STYLE.unchanged;
                  const isUnresolved = row.status === "unresolved";
                  const edited = edits[row.field];
                  const choices = choicesFor(vocabularies, row.field);
                  const mayEdit = mayEditField(permissions, vocabularies, row.field);
                  return (
                    <tr
                      key={row.field}
                      className="border-t"
                      style={{ borderColor: HAIRLINE }}
                    >
                      <td className="py-2 align-top">
                        <span style={{ color: NAVY }}>{row.field}</span>
                        {row.required ? (
                          <span className="ml-1" style={{ color: GOLD }}>
                            *
                          </span>
                        ) : null}
                        {isUnresolved && row.reason ? (
                          <p
                            className="mt-1 max-w-[46ch] text-[12px] leading-snug"
                            style={{ color: ERROR }}
                          >
                            {row.reason}
                          </p>
                        ) : null}
                        {errorsByField[row.field]?.map((e, i) => (
                          <p
                            key={i}
                            className="mt-1 max-w-[46ch] text-[12px] leading-snug"
                            style={{ color: ERROR }}
                          >
                            {e.message}
                          </p>
                        ))}
                      </td>
                      <td className="py-2 align-top" style={{ color: MUTED }}>
                        {fmtValue(row.current)}
                      </td>
                      <td className="py-2 align-top">
                        {/* Write controls render ONLY inside the can_write
                            check. No fallback — see the file header. */}
                        {mayEdit ? (
                          choices ? (
                            <select
                              aria-label={row.field}
                              value={edited?.corrected ?? row.proposed ?? ""}
                              onChange={(e) =>
                                setField(row.field, row.proposed, e.target.value)
                              }
                              className="rounded-[6px] border px-2 py-1 text-[14px]"
                              style={{
                                borderColor: isUnresolved ? ERROR : "#E2E8F0",
                                color: NAVY,
                              }}
                            >
                              <option value="">—</option>
                              {choices.map((c) => (
                                <option key={c} value={c}>
                                  {c}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              aria-label={row.field}
                              value={edited?.corrected ?? row.proposed ?? ""}
                              onChange={(e) =>
                                setField(row.field, row.proposed, e.target.value)
                              }
                              className="w-full rounded-[6px] border px-2 py-1 text-[14px]"
                              style={{
                                borderColor: isUnresolved ? ERROR : "#E2E8F0",
                                color: NAVY,
                              }}
                            />
                          )
                        ) : (
                          <span
                            style={{ color: style.color, fontWeight: style.weight }}
                          >
                            {fmtValue(row.proposed)}
                          </span>
                        )}
                      </td>
                      <td
                        className="py-2 align-top text-[12px] uppercase tracking-[0.1em]"
                        style={{ color: style.color, fontWeight: style.weight }}
                      >
                        {edited && edited.corrected !== edited.original
                          ? "edited by you"
                          : style.label}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {result.diff.tiers.length ? (
              <div className="mt-6">
                <h3
                  className="text-[13px] font-semibold uppercase tracking-[0.14em]"
                  style={{ color: MUTED }}
                >
                  Tiers
                </h3>
                <ul className="mt-2 space-y-1 text-[14px]" style={{ color: NAVY }}>
                  {result.diff.tiers.map((t) => {
                    const style = STATUS_STYLE[t.status] ?? STATUS_STYLE.unchanged;
                    const p = t.proposed ?? {};
                    return (
                      <li key={t.tier_seq}>
                        <span style={{ color: MUTED }}>{t.tier_seq}.</span>{" "}
                        {fmtValue(p.lower_bound)} –{" "}
                        {p.upper_bound ? fmtValue(p.upper_bound) : "no limit"} at{" "}
                        {fmtValue(p.rate_bps)} bps{" "}
                        <span
                          className="text-[12px] uppercase tracking-[0.1em]"
                          style={{ color: style.color }}
                        >
                          {style.label}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
          </section>

          {/* ── references needing a human choice ───────────────────── */}
          {result.references.disambiguation.length ||
          result.references.unresolved.length ? (
            <section className={CARD}>
              <h2
                className="text-[19px] font-semibold"
                style={{ color: NAVY, fontFamily: "Spectral, Georgia, serif" }}
              >
                Names still to confirm
              </h2>
              {result.references.disambiguation.map((r) => (
                <div key={r.ref} className="mt-3">
                  <p className="text-[14px]" style={{ color: NAVY }}>
                    “{r.name}” — {r.reason}
                  </p>
                  <ul className="mt-1 text-[13px]" style={{ color: MUTED }}>
                    {r.candidates.map((c) => (
                      <li key={c.id}>
                        {c.label}
                        {c.alt ? ` · ${c.alt}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {result.references.unresolved.map((r) => (
                <p key={r.ref} className="mt-3 text-[14px]" style={{ color: ERROR }}>
                  “{r.name}” — {r.reason}
                </p>
              ))}
            </section>
          ) : null}

          {/* ── the worked example ──────────────────────────────────── */}
          <section className={CARD}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2
                  className="text-[19px] font-semibold"
                  style={{ color: NAVY, fontFamily: "Spectral, Georgia, serif" }}
                >
                  What this would actually charge
                </h2>
                <p className="mt-1 max-w-[60ch] text-[13px]" style={{ color: MUTED }}>
                  Computed by the billing engine against a real account&rsquo;s
                  real balances — not an estimate, and not a figure the
                  assistant produced.
                </p>
              </div>
              <button
                type="button"
                onClick={onWorkedExample}
                disabled={busy !== null}
                className="shrink-0 rounded-[6px] border px-4 py-2 text-[14px] font-medium disabled:opacity-40"
                style={{ borderColor: NAVY, color: NAVY }}
              >
                {busy === "example" ? "Computing…" : "Compute"}
              </button>
            </div>

            {example ? (
              <div className="mt-4">
                <p
                  className="text-[34px] leading-none"
                  style={{ color: NAVY, fontFamily: "Spectral, Georgia, serif" }}
                >
                  {fmtMoney(example.amount, example.currency)}
                </p>
                <p className="mt-2 text-[13px]" style={{ color: MUTED }}>
                  Account {example.account_label} · {example.period_start} to{" "}
                  {example.period_end} · billable{" "}
                  {fmtMoney(example.billable_value, example.currency)} · engine{" "}
                  {example.engine_version}
                </p>
                {example.assumptions.length ? (
                  <ul className="mt-2 space-y-1 text-[12px]" style={{ color: MUTED }}>
                    {example.assumptions.map((a, i) => (
                      <li key={i}>· {a}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {exampleError ? (
              <p className="mt-4 max-w-[70ch] text-[14px]" style={{ color: ERROR }}>
                No figure can be computed yet. {exampleError.message}
              </p>
            ) : null}
          </section>

          {/* ── save ────────────────────────────────────────────────── */}
          {showSaveControl(permissions) ? (
            <section className={CARD}>
              <div className="flex items-center justify-between gap-4">
                <p className="text-[13px]" style={{ color: MUTED }}>
                  {validationErrors.length
                    ? `${validationErrors.length} rule(s) must be resolved before this can be saved.`
                    : "Saves as a draft. Approval is a separate step."}
                </p>
                <button
                  type="button"
                  onClick={onSave}
                  disabled={busy !== null || !saveEnabled(permissions, validationErrors)}
                  className="rounded-[6px] px-4 py-2 text-[14px] font-medium text-white disabled:opacity-40"
                  style={{ backgroundColor: NAVY }}
                >
                  {busy === "save" ? "Saving…" : "Save as draft"}
                </button>
              </div>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
