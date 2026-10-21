import { formatCurrency } from "@/lib/format";

// BUGFIX1 (PORT-02) — the caller's visible commitments (GET
// /portfolio/commitments); a row opens the existing projection screen.
export default function CommitmentsList({ rows, error = null }) {
  if (error) {
    return <p className="text-sm text-[var(--2a-text-secondary)]" data-testid="commitments-error">{error}</p>;
  }
  if (!rows?.length) {
    return (
      <p className="text-sm text-[var(--2a-text-secondary)]" data-testid="commitments-empty">
        No commitments are recorded for the entities you can see.
      </p>
    );
  }
  return (
    <table className="w-full text-sm" data-testid="commitments-table">
      <thead>
        <tr className="border-b text-left text-xs uppercase tracking-wide text-[var(--2a-text-muted)]"
            style={{ borderColor: "#ece8dd" }}>
          <th className="py-2 pr-3 font-semibold">Name</th>
          <th className="py-2 pr-3 font-semibold">Fund</th>
          <th className="py-2 pr-3 text-right font-semibold">Commitment</th>
          <th className="py-2 pr-3 text-right font-semibold">Called</th>
          <th className="py-2 text-right font-semibold">Remaining</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="border-b last:border-0" style={{ borderColor: "#ece8dd" }}>
            <td className="py-2 pr-3">
              <a href={r.href} className="font-medium text-[var(--2a-navy)] hover:underline">{r.name || "—"}</a>
            </td>
            <td className="py-2 pr-3">{r.fund || "—"}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{formatCurrency(r.commitment)}</td>
            <td className="py-2 pr-3 text-right tabular-nums">{formatCurrency(r.called)}</td>
            <td className="py-2 text-right tabular-nums">{formatCurrency(r.remaining)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
