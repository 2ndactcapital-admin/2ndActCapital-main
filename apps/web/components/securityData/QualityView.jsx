import { labelOf, vocab } from "./sourceLabel.mjs";

// MARKETDATA1 — data quality: stale, gap, outlier, disagreement and budget-exhausted events,
// per item and day. Read-only; labels from the server's vocabularies.
export default function QualityView({ envelope }) {
  const kinds = vocab(envelope, "quality_kinds");
  const types = vocab(envelope, "data_types");
  const events = envelope?.events || [];
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-5">
      {events.length === 0 ? (
        <p className="text-sm text-text-muted">No data-quality events.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th>Date</th><th>Event</th><th>Data</th><th>Item</th><th>Source</th><th>Detail</th></tr></thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} className="border-t border-[#E2E8F0]" data-testid="quality-row">
                <td>{e.event_date}</td>
                <td>{labelOf(kinds, e.kind)}</td>
                <td>{labelOf(types, e.data_type)}</td>
                <td>{e.security_name || e.subject_key}{e.owner_scope === "platform" ? " (platform)" : ""}</td>
                <td>{e.provider_key || "—"}</td>
                <td className="text-xs text-text-muted">{JSON.stringify(e.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
