// BUGFIX1 (PORT-01) — shown above an empty Positions / Securities grid when
// the API says why (`empty_hint`: wording and link from the server).
export default function EmptyHoldingsHint({ hint }) {
  if (!hint) return null;
  return (
    <div className="mb-4 bg-white px-5 py-4" style={{ border: "1px solid #ece8dd", borderRadius: 6 }}
         data-testid="empty-holdings-hint">
      <p className="text-sm text-text-secondary">{hint.message}</p>
      {hint.href && (
        <a href={hint.href} className="mt-2 inline-block text-sm font-medium text-navy underline underline-offset-4">
          {hint.href_label || hint.href}
        </a>
      )}
    </div>
  );
}
