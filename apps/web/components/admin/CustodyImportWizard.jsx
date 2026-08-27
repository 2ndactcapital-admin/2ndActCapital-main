"use client";

import { useCallback, useMemo, useState } from "react";

// Custody import wizard — upload → map → dry-run → commit.
//
// FOUR STEPS, AND THE FILE IS RE-POSTED AT EACH ONE.
// The File object stays in the browser between steps and is uploaded again for
// inspect, dry-run and commit. Holding it server-side between steps would mean
// a file full of unmasked account numbers sitting at rest on the web tier for
// as long as an operator takes to review a diff — which is the one thing this
// module's whole design is arranged to avoid. Three uploads of a CSV is cheap.
//
// NOTHING IS WRITTEN UNTIL COMMIT. The dry-run response and the commit response
// are computed by the same backend function, so the diff shown here is the diff
// that gets applied, not a separate preview that could drift from it.
//
// Colours come from the Tailwind token classes (navy / gold / border / bg-card /
// text-muted), which resolve to the org's own palette. No hex literals here.

const RECORD_KINDS = [
  {
    key: "account",
    label: "Accounts",
    hint: "Who the account belongs to and how it is registered",
    fields: [
      ["account_number", "Account number", true],
      ["primary_entity_ref", "Owner (entity name or id)", false],
      ["household_ref", "Household", false],
      ["registration_type", "Registration type", false],
      ["tax_status", "Tax status", false],
      ["service_model", "Service model", false],
      ["custodian_account_id", "Custodian's own account id", false],
      ["is_billable", "Billable?", false],
      ["is_discretionary", "Discretionary?", false],
      ["is_held_away", "Held away?", false],
      ["opened_on", "Opened on", false],
      ["closed_on", "Closed on", false],
      ["base_currency", "Base currency", false],
    ],
  },
  {
    key: "balance",
    label: "Balances",
    hint: "One row per account per day",
    fields: [
      ["account_number", "Account number", true],
      ["as_of_date", "As-of date", true],
      ["total_market_value", "Total market value", true],
      ["cash_value", "Cash", false],
      ["margin_balance", "Margin balance", false],
      ["accrued_income", "Accrued income", false],
    ],
  },
  {
    key: "flow",
    label: "Flows",
    hint: "Contributions, withdrawals and transfers",
    fields: [
      ["account_number", "Account number", true],
      ["flow_date", "Flow date", true],
      ["amount", "Amount", true],
      ["flow_type", "Flow type", false],
      ["is_billable_flow", "Counts for billing?", false],
    ],
  },
];

const CARD = "rounded border border-border bg-bg-card p-6";
const LABEL =
  "text-xs font-semibold uppercase tracking-[0.22em] text-gold";

export default function CustodyImportWizard({
  initialProfiles = [],
  canWrite = false,
  initialBatches = [],
}) {
  const [custodianCode, setCustodianCode] = useState(
    initialProfiles[0]?.custodian_code || "",
  );
  const [file, setFile] = useState(null);
  const [inspection, setInspection] = useState(null);
  const [columnMap, setColumnMap] = useState({});
  const [plan, setPlan] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [batches, setBatches] = useState(initialBatches);

  const step = result ? 4 : plan ? 3 : inspection ? 2 : 1;

  const post = useCallback(
    async (path, extra = {}) => {
      const body = new FormData();
      body.append("custodian_code", custodianCode);
      body.append("file", file);
      for (const [key, value] of Object.entries(extra)) {
        body.append(key, value);
      }
      const res = await fetch(path, { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      return data;
    },
    [custodianCode, file],
  );

  const run = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);

  const onInspect = () =>
    run(async () => {
      const data = await post("/api/custody/import/inspect");
      setInspection(data);
      // Seed the mapping from the profile's suggestion, but only where the
      // suggested column actually exists in THIS file. Pre-filling a column the
      // file does not have looks like a completed mapping and fails at dry-run.
      const headers = new Set(data.headers || []);
      const seeded = {};
      for (const kind of RECORD_KINDS) {
        const suggested = data.suggested_column_map?.[kind.key] || {};
        seeded[kind.key] = Object.fromEntries(
          Object.entries(suggested).filter(([, column]) => headers.has(column)),
        );
      }
      setColumnMap(seeded);
      setPlan(null);
      setResult(null);
    });

  const onDryRun = () =>
    run(async () => {
      const data = await post("/api/custody/import/dry-run", {
        column_map: JSON.stringify(columnMap),
      });
      setPlan(data);
      setResult(null);
    });

  const onCommit = () =>
    run(async () => {
      const data = await post("/api/custody/import/commit", {
        column_map: JSON.stringify(columnMap),
      });
      setResult(data);
      const refreshed = await fetch("/api/custody/batches");
      if (refreshed.ok) {
        const payload = await refreshed.json();
        setBatches(payload.rows || []);
      }
    });

  const reset = () => {
    setFile(null);
    setInspection(null);
    setColumnMap({});
    setPlan(null);
    setResult(null);
    setError(null);
  };

  const counts = plan?.counts;
  const readyToMap = Boolean(inspection?.headers?.length);

  const mappedKinds = useMemo(
    () =>
      RECORD_KINDS.filter((kind) => {
        const mapping = columnMap[kind.key] || {};
        return kind.fields
          .filter(([, , required]) => required)
          .every(([field]) => mapping[field]);
      }).map((kind) => kind.key),
    [columnMap],
  );

  return (
    <div className="mt-6 space-y-6">
      <Steps current={step} />

      {error ? (
        <div className="rounded border border-border bg-bg-card p-4 text-sm text-gold">
          {error}
        </div>
      ) : null}

      {!canWrite ? (
        <div className={`${CARD} text-sm text-text-muted`}>
          You may review past import batches, but committing an import requires
          the billing-management permission.
        </div>
      ) : null}

      {/* ── Step 1 — upload ───────────────────────────────────────────── */}
      <section className={CARD}>
        <p className={LABEL}>Step 1 — File</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-sm text-text-secondary">Custodian</span>
            <select
              value={custodianCode}
              onChange={(e) => {
                setCustodianCode(e.target.value);
                reset();
              }}
              disabled={!canWrite}
              className="mt-1 w-full rounded border border-border bg-bg-card px-3 py-2 text-sm"
            >
              {initialProfiles.map((profile) => (
                <option key={profile.custodian_code} value={profile.custodian_code}>
                  {profile.label}
                  {profile.is_default ? " (platform default)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-sm text-text-secondary">CSV export</span>
            <input
              type="file"
              accept=".csv,.txt,text/csv"
              disabled={!canWrite}
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setInspection(null);
                setPlan(null);
                setResult(null);
              }}
              className="mt-1 w-full rounded border border-border bg-bg-card px-3 py-2 text-sm"
            />
          </label>
        </div>
        <button
          type="button"
          onClick={onInspect}
          disabled={!canWrite || !file || !custodianCode || busy}
          className="mt-4 rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy && step === 1 ? "Reading…" : "Read columns"}
        </button>
      </section>

      {/* ── Step 2 — map ──────────────────────────────────────────────── */}
      {readyToMap ? (
        <section className={CARD}>
          <p className={LABEL}>Step 2 — Map columns</p>
          <p className="mt-2 text-sm text-text-muted">
            {inspection.row_count} data rows · {inspection.headers.length}{" "}
            columns. Leave a whole section unmapped if the file does not carry
            it — a balances-only export is normal.
          </p>

          <div className="mt-5 space-y-6">
            {RECORD_KINDS.map((kind) => (
              <div key={kind.key}>
                <div className="flex items-baseline gap-3">
                  <h3 className="font-medium text-navy">{kind.label}</h3>
                  <span className="text-xs text-text-muted">{kind.hint}</span>
                  {mappedKinds.includes(kind.key) ? (
                    <span className="text-xs text-navy">ready</span>
                  ) : null}
                </div>
                <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {kind.fields.map(([fieldName, fieldLabel, required]) => (
                    <label key={fieldName} className="block">
                      <span className="text-xs text-text-secondary">
                        {fieldLabel}
                        {required ? <span className="text-gold"> *</span> : null}
                      </span>
                      <select
                        value={columnMap[kind.key]?.[fieldName] || ""}
                        onChange={(e) =>
                          setColumnMap((previous) => ({
                            ...previous,
                            [kind.key]: {
                              ...(previous[kind.key] || {}),
                              [fieldName]: e.target.value,
                            },
                          }))
                        }
                        className="mt-1 w-full rounded border border-border bg-bg-card px-2 py-1.5 text-sm"
                      >
                        <option value="">— not in this file —</option>
                        {inspection.headers.map((header) => (
                          <option key={header} value={header}>
                            {header}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <SamplePreview rows={inspection.sample_rows} headers={inspection.headers} />

          <button
            type="button"
            onClick={onDryRun}
            disabled={busy || mappedKinds.length === 0}
            className="mt-5 rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy && step === 2 ? "Checking…" : "Preview changes"}
          </button>
        </section>
      ) : null}

      {/* ── Step 3 — dry-run diff ─────────────────────────────────────── */}
      {plan ? (
        <section className={CARD}>
          <p className={LABEL}>Step 3 — Review · nothing written yet</p>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="New accounts" value={counts.accounts_new} />
            <Stat label="Changed accounts" value={counts.accounts_changed} />
            <Stat label="New balances" value={counts.balances_new} />
            <Stat label="Changed balances" value={counts.balances_changed} />
            <Stat label="Unchanged balances" value={counts.balances_unchanged} />
            <Stat label="New flows" value={counts.flows_new} />
            <Stat label="Duplicate flows" value={counts.flows_duplicate} />
            <Stat label="Unmatched rows" value={counts.unmatched} emphasis />
          </div>

          <DiffTable
            title="Accounts"
            empty="No accounts in this file."
            rows={plan.accounts}
            columns={[
              ["action", "Action"],
              ["account_number_masked", "Account"],
              ["primary_entity_ref", "Owner"],
              ["registration_type", "Registration"],
              ["base_currency", "Ccy"],
            ]}
          />
          <DiffTable
            title="Balances"
            empty="No balances in this file."
            rows={plan.balances.filter((row) => row.action !== "unchanged")}
            columns={[
              ["action", "Action"],
              ["account_number_masked", "Account"],
              ["as_of_date", "As of"],
              ["total_market_value", "Market value"],
              ["cash_value", "Cash"],
            ]}
          />
          <DiffTable
            title="Flows"
            empty="No flows in this file."
            rows={plan.flows}
            columns={[
              ["action", "Action"],
              ["account_number_masked", "Account"],
              ["flow_date", "Date"],
              ["flow_type", "Type"],
              ["amount", "Amount"],
            ]}
          />

          {/* Unmatched rows are their own list, deliberately not merged into
              the three above. They are a different decision: somebody has to
              go and create the missing entity, not approve a write. */}
          <div className="mt-6">
            <h3 className="font-medium text-navy">
              Unmatched rows ({plan.unmatched.length})
            </h3>
            {plan.unmatched.length === 0 ? (
              <p className="mt-2 text-sm text-text-muted">
                Every row resolved.
              </p>
            ) : (
              <>
                <p className="mt-1 text-sm text-text-muted">
                  These are kept on the batch as an exception list rather than
                  dropped, and they do not stop the rest of the file importing.
                </p>
                <table className="mt-2 w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                      <th className="py-2 pr-3">Line</th>
                      <th className="py-2 pr-3">Kind</th>
                      <th className="py-2 pr-3">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.unmatched.map((row, index) => (
                      <tr key={index} className="border-b border-border/60">
                        <td className="py-2 pr-3 tabular-nums">{row.source_row}</td>
                        <td className="py-2 pr-3">{row.record_kind}</td>
                        <td className="py-2 pr-3 text-text-secondary">{row.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>

          <button
            type="button"
            onClick={onCommit}
            disabled={!canWrite || busy}
            className="mt-6 rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy && step === 3 ? "Committing…" : "Commit import"}
          </button>
        </section>
      ) : null}

      {/* ── Step 4 — result ───────────────────────────────────────────── */}
      {result ? (
        <section className={CARD}>
          <p className={LABEL}>Step 4 — Committed</p>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Accounts created" value={result.accounts_created} />
            <Stat label="Accounts updated" value={result.accounts_updated} />
            <Stat label="Balances created" value={result.balances_created} />
            <Stat label="Balances updated" value={result.balances_updated} />
            <Stat label="Flows created" value={result.flows_created} />
            <Stat label="Flows skipped" value={result.flows_skipped_duplicate} />
            <Stat label="Exceptions" value={result.exceptions} emphasis />
          </div>
          <p className="mt-3 text-sm text-text-muted">
            Batch {result.batch_id}. Re-running this identical file will create
            no further balance or flow rows.
          </p>
          <button
            type="button"
            onClick={reset}
            className="mt-4 rounded border border-border px-4 py-2 text-sm text-navy"
          >
            Import another file
          </button>
        </section>
      ) : null}

      <BatchHistory rows={batches} />
    </div>
  );
}

function Steps({ current }) {
  const labels = ["Upload", "Map", "Review", "Commit"];
  return (
    <ol className="flex flex-wrap gap-x-6 gap-y-2 text-xs uppercase tracking-[0.22em]">
      {labels.map((label, index) => (
        <li
          key={label}
          className={
            index + 1 === current
              ? "font-semibold text-gold"
              : index + 1 < current
                ? "text-navy"
                : "text-text-muted"
          }
        >
          {index + 1}. {label}
        </li>
      ))}
    </ol>
  );
}

function Stat({ label, value, emphasis }) {
  return (
    <div className="rounded border border-border px-3 py-2">
      <div className="text-xs text-text-muted">{label}</div>
      <div
        className={`text-xl tabular-nums ${
          emphasis && value > 0 ? "text-gold" : "text-navy"
        }`}
      >
        {value ?? 0}
      </div>
    </div>
  );
}

function SamplePreview({ rows, headers }) {
  if (!rows?.length) return null;
  return (
    <div className="mt-6">
      <h3 className="font-medium text-navy">Sample rows</h3>
      <p className="mt-1 text-xs text-text-muted">
        Anything account-number shaped is masked before it leaves the server, so
        this preview never shows a full account number.
      </p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border text-text-muted">
              {headers.map((header) => (
                <th key={header} className="whitespace-nowrap py-2 pr-4">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-b border-border/60">
                {headers.map((header) => (
                  <td key={header} className="whitespace-nowrap py-1.5 pr-4">
                    {row[header]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DiffTable({ title, rows, columns, empty }) {
  return (
    <div className="mt-6">
      <h3 className="font-medium text-navy">
        {title} ({rows.length})
      </h3>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">{empty}</p>
      ) : (
        <div className="mt-2 max-h-80 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                {columns.map(([, label]) => (
                  <th key={label} className="py-2 pr-3">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index} className="border-b border-border/60">
                  {columns.map(([key]) => (
                    <td key={key} className="py-1.5 pr-3">
                      {row[key] ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function BatchHistory({ rows }) {
  return (
    <section className={CARD}>
      <p className={LABEL}>Recent imports</p>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No imports yet.</p>
      ) : (
        <table className="mt-3 w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
              <th className="py-2 pr-3">When</th>
              <th className="py-2 pr-3">Custodian</th>
              <th className="py-2 pr-3">File</th>
              <th className="py-2 pr-3">Rows</th>
              <th className="py-2 pr-3">Matched</th>
              <th className="py-2 pr-3">Unmatched</th>
              <th className="py-2 pr-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b border-border/60">
                <td className="py-2 pr-3">
                  {new Date(row.created_at).toLocaleString()}
                </td>
                <td className="py-2 pr-3">{row.custodian_code}</td>
                <td className="py-2 pr-3">{row.source_filename || "—"}</td>
                <td className="py-2 pr-3 tabular-nums">{row.row_count}</td>
                <td className="py-2 pr-3 tabular-nums">{row.matched_count}</td>
                <td
                  className={`py-2 pr-3 tabular-nums ${
                    row.unmatched_count > 0 ? "text-gold" : ""
                  }`}
                >
                  {row.unmatched_count}
                </td>
                <td className="py-2 pr-3">{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
