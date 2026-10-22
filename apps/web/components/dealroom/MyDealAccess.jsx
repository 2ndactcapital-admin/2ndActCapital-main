/**
 * MyDealAccess — the investor's own deals, what they signed and when, with the
 * signed copies (DEALROOM1). Pure render of GET /deal-room/my-access, which is
 * self-scoped server-side: it carries no other investor's names or counts.
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
          {r.signatures.length > 0 ? (
            <ul className="mt-3 space-y-1 text-sm text-text-secondary">
              {r.signatures.map((s) => (
                <li key={s.signature_id}>
                  {s.title} (v{s.version}) — signed {s.signed_at.slice(0, 10)} ·{" "}
                  <a href={s.copy_href} target="_blank" rel="noopener" className="text-navy hover:underline">signed copy</a>
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
