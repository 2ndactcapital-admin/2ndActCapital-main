/**
 * ROLLUP2 — the parts of a net-worth figure that used to be invisible: the valued holdings with no allocation bucket
 * (counted, under "Unclassified"), the holdings with no value (listed, not counted), the household's Family /
 * Charitable / Household split and its Owners list. Pure render from the API's own envelope (lib/rollup2.mjs).
 */

import { ownerRows, skippedRows, splitRows, unclassifiedLabel, unclassifiedRows } from "@/lib/rollup2.mjs";
import { money } from "@/lib/heldAway.mjs";

const quiet = "text-xs text-[#64748B]";

export function UnclassifiedList({ netWorth, vocabularies }) {
  const rows = unclassifiedRows(netWorth);
  const skipped = skippedRows(netWorth);
  if (!rows.length && !skipped.length) return null;
  const label = unclassifiedLabel(vocabularies);
  return (
    <div className="mt-3 space-y-2 text-sm">
      {rows.length ? (
        <div>
          <p className={quiet}>{label} — counted in net worth, no allocation bucket yet ({money(netWorth?.unclassified_value)})</p>
          <ul className="mt-1">{rows.map((r) => (
            <li key={r.id} className="flex justify-between border-t border-[#ece8dd] py-1">
              <span className="text-[#0F172A]">{r.label}{r.owner ? <span className={quiet}> · {r.owner}</span> : null}</span>
              <span className="text-[#1B2B4B]">{money(r.value)}</span>
            </li>
          ))}</ul>
        </div>
      ) : null}
      {skipped.length ? (
        <div>
          <p className="text-xs text-[#9B2335]">{skipped.length} holding(s) have no value and are not counted</p>
          <ul className="mt-1">{skipped.map((r) => (
            <li key={r.id} className={`border-t border-[#ece8dd] py-1 ${quiet}`}>{r.label}{r.owner ? ` · ${r.owner}` : ""} — {r.reason}</li>
          ))}</ul>
        </div>
      ) : null}
    </div>
  );
}

export function SplitTable({ total, vocabularies }) {
  const rows = splitRows(total, vocabularies);
  if (!rows) return null;
  return (
    <table className="mt-3 w-full text-sm">
      <thead className="text-left text-[#64748B]"><tr><th className="py-1" /><th>Gross</th><th>Liabilities</th><th>Net</th><th>{unclassifiedLabel(vocabularies)}</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.key} className={`border-t border-[#ece8dd] ${r.key === "household" ? "font-semibold text-[#1B2B4B]" : ""}`}>
          <td className="py-1">{r.label}{r.entities.length ? <span className={quiet}> · {r.entities.join(", ")}</span> : null}</td>
          <td>{money(r.gross)}</td><td>{money(r.liabilities)}</td><td>{money(r.net)}</td><td>{money(r.unclassified)}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

export function OwnersTable({ total, vocabularies }) {
  const rows = ownerRows(total);
  if (!rows.length) return null;
  return (
    <table className="mt-3 w-full text-sm">
      <thead className="text-left text-[#64748B]"><tr><th className="py-1">Owners</th><th>Gross</th><th>Liabilities</th><th>Net worth</th><th>{unclassifiedLabel(vocabularies)}</th></tr></thead>
      <tbody>{rows.map((o) => (
        <tr key={o.id} className="border-t border-[#ece8dd]">
          <td className="py-1"><a className="text-[#1B2B4B] hover:underline" href={`/crm/${o.id}?tab=liabilities`}>{o.name}</a>{o.charitable ? <span className={quiet}> · charitable</span> : null}</td>
          <td>{money(o.gross)}</td><td>{money(o.liabilities)}</td><td>{money(o.net)}</td><td>{o.unclassified ? money(o.unclassified) : "—"}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}
