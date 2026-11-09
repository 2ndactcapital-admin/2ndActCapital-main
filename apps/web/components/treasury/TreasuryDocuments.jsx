"use client";

/**
 * TreasuryDocuments — Household → Treasury → Documents (CASH05): statements, trade confirmations and 1099s of the
 * family's cash accounts, from Chancery, every date in Eastern time; open / download through the existing document
 * route (no new storage). Read-only; lib/cashTreasury.mjs fails closed. Light / cream.
 */

import { documentRows, documentsEmpty, readable } from "@/lib/cashTreasury.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const serif = { fontFamily: "'Spectral', Georgia, serif" };

/** The existing download route answers {url} (a presigned link): fetch it, then open the file. */
export async function openDocument(href) {
  const res = await fetch(href);
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.url) window.open(data.url, "_blank", "noopener");
}

export default function TreasuryDocuments({ data }) {
  if (!readable(data)) return null;
  const rows = documentRows(data);
  const empty = documentsEmpty(data);
  return (
    <section id="treasury-documents" className="space-y-2" aria-label="Documents">
      <h3 className="text-lg font-semibold text-navy" style={serif}>Documents</h3>
      <p className="text-xs text-text-muted">Dates are Eastern time ({data.timezone}). {data.date_basis}.</p>
      {empty ? <p className={`${card} text-sm text-text-muted`} data-empty="documents">{empty}</p> : (
        <table className={`${card} w-full text-sm`}>
          <thead className="text-left text-text-muted"><tr><th className="py-1">Date (ET)</th><th>Document</th><th>Type</th><th>Account</th><th /></tr></thead>
          <tbody>{rows.map((d) => (
            <tr key={d.id} className="border-t border-[#ece8dd] align-top">
              <td className="py-1" title={d.datetime}>{d.date}</td><td>{d.name}</td><td>{d.type}</td><td>{d.account}</td>
              <td>{d.open ? <a className="text-navy underline" href={d.open.href}
                onClick={(e) => { e.preventDefault(); openDocument(d.open.href); }}>{d.open.label}</a>
                : <span className="text-xs text-text-muted">{d.fileText}</span>}</td>
            </tr>))}
          </tbody>
        </table>)}
    </section>
  );
}
