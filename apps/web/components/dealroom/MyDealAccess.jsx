/**
 * MyDealAccess — the investor's own deals, what they signed and when, with the
 * signed copies (DEALROOM1). Pure render of GET /deal-room/my-access, which is
 * self-scoped server-side: it carries no other investor's names or counts.
 * DEALROOM2: each signature shows its agreement version and links the PDF
 * copy (with its certificate page); a deal needing re-signing lists what to
 * sign.
 */
export default function MyDealAccess({ rows = [] }) {
  if (!rows.length) {
    return (
      <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
        You have not requested access to any deal yet.
      </div>
    );
  }
  return (
    <ul className="space-y-4" data-testid="my-deal-access">
      {rows.map((r) => (
        <li key={r.deal_id} className="rounded-[6px] border border-[#ece8dd] bg-white p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <a href={`/marketplace/${r.deal_id}?tab=documents`} className="text-base font-semibold text-navy hover:underline">
              {r.deal_name}
            </a>
            <span className="text-sm text-text-secondary">
              {r.status_label}{r.level_label ? ` · ${r.level_label}` : ""}
            </span>
          </div>
          {r.entity_name && <p className="mt-1 text-xs text-text-muted">Investing through {r.entity_name}</p>}
          {r.status === "resign_required" && (
            <div className="mt-3 rounded border border-[#9B2335]/30 p-3 text-sm text-text-secondary" data-testid="to-sign">
              <p className="text-[#9B2335]">Re-signing required. Sign the updated agreement{(r.to_sign || []).length > 1 ? "s" : ""} to
                reopen the data room:</p>
              <ul className="mt-1 list-disc pl-5">
                {(r.to_sign || []).map((t) => <li key={t.agreement_id}>{t.title}{t.version ? ` (version ${t.version})` : ""}</li>)}
              </ul>
              <a href={`/marketplace/${r.deal_id}?tab=documents`} className="mt-1 inline-block text-navy hover:underline">Sign now →</a>
            </div>
          )}
          {r.signatures.length > 0 ? (
            <ul className="mt-3 space-y-1 text-sm text-text-secondary">
              {r.signatures.map((s) => (
                <li key={s.signature_id}>
                  {s.title} (v{s.version}) — signed {s.signed_at.slice(0, 10)}
                  {s.entity_name ? ` for ${s.entity_name}` : ""} ·{" "}
                  <a href={s.copy_href} target="_blank" rel="noopener" className="text-navy hover:underline">signed copy</a>
                  {s.pdf_href && (
                    <>
                      {" "}·{" "}
                      <a href={s.pdf_href} className="text-navy hover:underline">PDF with certificate</a>
                    </>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-text-muted">No agreements signed yet.</p>
          )}
        </li>
      ))}
    </ul>
  );
}
