/**
 * MyDealAccess — the investor's own deals, what they signed and when, with the
 * signed copies (DEALROOM1). Pure render of GET /deal-room/my-access, which is
 * self-scoped server-side: it carries no other investor's names or counts.
 * DEALROOM2: each signature shows its agreement version and links the PDF
 * copy (with its certificate page); a deal needing re-signing lists what to
 * sign.
 * DEALROOM3: the access end date or paused reason, the investor's advisors, a
 * return-or-destroy notice (acknowledged here), and the deals the caller
 * advises someone on.
 */
import { accessLine, advisorLines, noticePending } from "@/lib/dealRoom.mjs";
import { ReturnNotice } from "@/components/dealroom/DataRoom";

export default function MyDealAccess({ rows = [], advising = [] }) {
  const advisingList = advising.length > 0 && (
    <section className="mb-4 rounded-[6px] border border-[#ece8dd] bg-white p-5" data-testid="advising">
      <h3 className="text-base font-semibold text-navy">Deals you advise on</h3>
      <ul className="mt-2 space-y-1 text-sm text-text-secondary">
        {advising.map((a) => (
          <li key={a.deal_id}>
            <a href={`/marketplace/${a.deal_id}?tab=documents`} className="text-navy hover:underline">{a.deal_name}</a>
            {` — for ${a.investor_name} (${a.role_label || "advisor"}) · ${a.status_label || a.status}`}
            {a.needs_ack ? " · acknowledgement needed" : ""}
          </li>
        ))}
      </ul>
    </section>
  );
  if (!rows.length) {
    if (advisingList) return advisingList;
    return (
      <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
        You have not requested access to any deal yet.
      </div>
    );
  }
  return (
    <>
    {advisingList}
    <ul className="space-y-4" data-testid="my-deal-access">
      {rows.map((r) => (
        <li key={r.deal_id} className="rounded-[6px] border border-[#ece8dd] bg-white p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <a href={`/marketplace/${r.deal_id}?tab=documents`} className="text-base font-semibold text-navy hover:underline">
              {r.deal_name}
            </a>
            <span className="text-sm text-text-secondary">
              {accessLine(r)}{r.level_label ? ` · ${r.level_label}` : ""}
            </span>
          </div>
          {(r.advisors || []).length > 0 && (
            <ul className="mt-2 text-xs text-text-muted" data-testid="my-advisors">
              {advisorLines(r.advisors).map((l) => <li key={l}>Advisor: {l}</li>)}
            </ul>
          )}
          {r.return_notice && !noticePending(r.return_notice) && (
            <p className="mt-2 text-xs text-text-muted">Return-or-destroy notice acknowledged {r.return_notice.acknowledged_at.slice(0, 10)}.</p>
          )}
          {noticePending(r.return_notice) && <ReturnNotice dealId={r.deal_id} notice={r.return_notice} />}
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
    </>
  );
}
